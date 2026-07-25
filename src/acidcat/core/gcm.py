"""GameCube disc image (GCM / ISO) filesystem walk.

A GameCube disc is not ISO 9660. Its own format: a header carrying the magic
word 0xC2339F3D at 0x1C, a pointer at 0x424 to the FST (File String Table), and
the FST itself -- a flat array of 12-byte entries (file or directory) plus a
trailing string table. Directories store the index one past their last child,
so the tree is walked by index ranges, not nesting.

    from acidcat.core import gcm
    for f in gcm.walk("game.iso"):
        print(f["path"], f["offset"], f["size"])
"""

import struct

MAGIC = 0xC2339F3D                   # GameCube disc magic word, at offset 0x1C
_MAGIC_OFF = 0x1C


def is_gcm(path):
    """True if `path` is a GameCube disc image (by the 0x1C magic word)."""
    try:
        with open(path, "rb") as f:
            f.seek(_MAGIC_OFF)
            return struct.unpack(">I", f.read(4))[0] == MAGIC
    except (OSError, struct.error):
        return False


def _entry(fst, i):
    return struct.unpack_from(">I", fst, i * 12)[0], \
        struct.unpack_from(">II", fst, i * 12 + 4)


def walk(path):
    """Yield {path, offset, size} for every file on a GameCube disc, or nothing
    if `path` is not one. offset/size are absolute byte positions in the image."""
    with open(path, "rb") as f:
        head = f.read(0x440)
        if len(head) < 0x440 or struct.unpack_from(">I", head, _MAGIC_OFF)[0] != MAGIC:
            return
        fst_off, fst_size = struct.unpack_from(">II", head, 0x424)
        f.seek(fst_off)
        fst = f.read(fst_size)
    if len(fst) < 12:
        return
    num = struct.unpack_from(">I", fst, 8)[0]         # root entry's size = entry count
    strtab = num * 12
    if strtab > len(fst):
        return

    def name(i):
        no = struct.unpack_from(">I", fst, i * 12)[0] & 0xFFFFFF
        end = fst.find(b"\x00", strtab + no)
        return fst[strtab + no:end].decode("latin-1", "replace")

    out = []

    def descend(idx, prefix, end):
        while idx < end and idx < num:
            typ = fst[idx * 12]
            off, size = struct.unpack_from(">II", fst, idx * 12 + 4)
            nm = name(idx)
            if typ == 1:                              # directory: size = index past last child
                descend(idx + 1, prefix + nm + "/", size)
                idx = size
            else:
                out.append({"path": prefix + nm, "offset": off, "size": size})
                idx += 1

    descend(1, "", num)
    yield from out


def read_file(path, entry, limit=None):
    """Read a file's bytes from its walk() entry (absolute offset/size)."""
    n = entry["size"] if limit is None else min(entry["size"], limit)
    with open(path, "rb") as f:
        f.seek(entry["offset"])
        return f.read(n)
