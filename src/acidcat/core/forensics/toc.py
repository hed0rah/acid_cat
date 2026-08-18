"""Find a table of contents in a container nobody has written a walker for.

acidcat supports audio formats, major and obscure. It deliberately does not
try to know what every *container* is -- what a Wii disc or a game mod archive
happens to be is not its business, and a walker per proprietary archive is a
losing game. What it does need is the ability to open one anyway and find the
audio inside.

A signature sweep can do that, and does. But a huge family of archives carries
something far better than a signature: an index. The shape recurs because it is
the obvious way to write one --

    <length> <name bytes> <fixed-width integer fields> <length> <name> ...

-- a length-prefixed name followed by a couple of sizes or offsets, repeated.
Anything that finds it turns an anonymous blob into a named list, which is the
difference between "22 regions" and "Sounds/Music/AnahitasLure.ogg".

This detects that shape without knowing any format. It reports a hypothesis in
the voice `--force` uses: candidate entries with the layout that produced them,
never an identification. The evidence is the chain -- a layout that predicts
where the next entry starts, over and over, is not a coincidence, and a wrong
layout stops chaining within an entry or two.
"""

import re
import struct

# A name has to look like one, and "contains a dot or a slash" is not enough.
#
# Measured on freedoom1.wad: this returned a 100-entry table at confidence 0.84
# whose names were the CONCRETE wall texture. Eight-bit image data sits in a
# narrow band of byte values, that band overlaps printable ASCII, and 0x5C is an
# ordinary pixel -- so a run of graphics satisfied "looks like a Windows path".
# The same is true of 8-bit audio. A detector aimed at finding audio inside
# unknown containers was being fooled by the audio.
#
# So the test is what actually separates a filename from smooth bytes that
# happen to be printable:
#
#   mostly alphanumeric   `Sounds/Music/Lure.ogg` is ~86% [A-Za-z0-9]; the
#                         texture was 39%, the rest being []^_` -- punctuation
#                         that is contiguous with the letters in ASCII and so
#                         appears wherever a smooth ramp crosses that range
#   a structured separator   a dot needs a short alphanumeric extension after
#                         it, a slash needs name characters on both sides. In
#                         the texture the backslashes sat between [ and ], which
#                         is not a path, it is a coincidence
#
# Both are cheap, and each one alone rejects the specimen that motivated them.
_NAME = re.compile(rb"[\x20-\x7e]{3,255}")
_SEPARATOR = re.compile(rb"[./\\]")
_EXTENSION = re.compile(rb"\.[A-Za-z0-9]{1,5}$")
_PATH_SEP = re.compile(rb"[A-Za-z0-9_)\]-][/\\][A-Za-z0-9_(\[-]")
_ALNUM_FLOOR = 0.6

# A filename draws on several ASCII classes -- a dot at 0x2E, digits at 0x30,
# capitals at 0x41, lowercase at 0x61 -- so its byte values are spread wide.
# `CalamityModMusic.dll` spans 75. Smooth 8-bit data crosses printable ASCII as
# a narrow contiguous ramp: the second false table freedoom produced spanned
# NINE values, [ through d, and every "name" in it was letters and the
# punctuation that happens to sit between them in the ASCII table. Alphanumeric
# ratio cannot see this, because a, b, c and d are alphanumeric.
_MIN_BYTE_SPAN = 32


def _alnum_ratio(name):
    if not name:
        return 0.0
    n = sum(1 for c in name if (48 <= c <= 57) or (65 <= c <= 90)
            or (97 <= c <= 122))
    return n / float(len(name))


def _looks_like_a_name(name):
    """Does this run of printable bytes plausibly name something?

    Deliberately strict. A missed table is a container we cannot read yet; a
    false table is a confident wrong answer with a filename attached to it, and
    this function exists because the second one shipped.
    """
    if not _SEPARATOR.search(name):
        return False
    if max(name) - min(name) < _MIN_BYTE_SPAN:
        return False
    if _alnum_ratio(name) < _ALNUM_FLOOR:
        return False
    # the separator has to be doing a separator's job
    return bool(_EXTENSION.search(name) or _PATH_SEP.search(name))

# No internal read cap. The caller already chooses how much to hand over --
# the TUI passes the first 2 MB -- and a second, hidden truncation inside would
# mean a table just past it went missing with nobody in a position to say so.
# Whoever picks the window owns the consequence of picking it.
# Measured, not guessed: over 137 ordinary audio files (wav/mp3/flac/ogg/aiff/
# midi and several preset formats) the longest chance chain was 7 entries, and
# only five files produced one at all. Twelve is comfortably clear of that while
# a real table runs to hundreds -- the tModLoader archive this was built against
# has 266.
_MIN_ENTRIES = 12
_MAX_FIELDS = 4                  # int32 fields between a name and the next


def _varint_before(data, at):
    """(value, start) for a 7-bit varint ending just before `at`, or None."""
    for width in (1, 2, 3):
        start = at - width
        if start < 0:
            return None
        run = data[start:at]
        if all(b & 0x80 for b in run[:-1]) and not (run[-1] & 0x80):
            value = 0
            for i, b in enumerate(run):
                value |= (b & 0x7F) << (7 * i)
            return value, start
    return None


def _prefix_at(data, at, kind):
    """(declared_length, entry_start) for the length prefix before `at`."""
    if kind == "varint":
        return _varint_before(data, at)
    width, fmt = {"u8": (1, "B"), "u16le": (2, "<H"), "u16be": (2, ">H"),
                  "u32le": (4, "<I"), "u32be": (4, ">I")}[kind]
    start = at - width
    if start < 0 or at > len(data):
        return None
    return struct.unpack_from(fmt, data, start)[0], start


_PREFIXES = ("varint", "u8", "u16le", "u16be", "u32le", "u32be")


def _chain_from(data, name_at, kind, nfields, field_width=4):
    """Entries found by repeatedly applying one layout from `name_at`."""
    out = []
    n = len(data)
    at = name_at
    while len(out) < 100000:
        got = _prefix_at(data, at, kind)
        if got is None:
            break
        length, start = got
        if not (3 <= length <= 255) or at + length > n:
            break
        name = data[at:at + length]
        if not _NAME.fullmatch(name):
            break
        after = at + length
        end = after + nfields * field_width
        if end > n:
            break
        raw = data[after:end]
        # If every byte of the "fields" is printable, they are text being read
        # as integers -- the other half of how prose chained.
        if all(0x20 <= b <= 0x7E for b in raw):
            break
        fields = [struct.unpack_from("<i", data, after + i * field_width)[0]
                  for i in range(nfields)]
        if any(f < 0 for f in fields):
            break
        out.append({"offset": start, "name": name.decode("utf-8", "replace"),
                    "fields": fields, "name_at": at, "end": end})
        # the next entry begins with its own prefix at `end`
        step = _prefix_width(data, end, kind)
        if step is None:
            break
        at = end + step
    return out


def _prefix_width(data, at, kind):
    """How many bytes the prefix starting at `at` occupies, or None."""
    if kind != "varint":
        return {"u8": 1, "u16le": 2, "u16be": 2, "u32le": 4, "u32be": 4}[kind]
    for width in (1, 2, 3):
        if at + width > len(data):
            return None
        if not (data[at + width - 1] & 0x80):
            return width
    return None


def find_toc(data, min_entries=_MIN_ENTRIES):
    """The best table-of-contents hypothesis in `data`, or None.

    Returns {"entries": [...], "prefix": kind, "fields": n, "offset": start,
    "confidence": float}. `entries` carry a name and the integer fields that
    followed it, which for every archive of this shape are its size and offset.
    """
    best = None
    seen_starts = set()
    for m in _NAME.finditer(data):
        name_at = m.start()
        if name_at in seen_starts or not _looks_like_a_name(m.group(0)):
            continue
        for kind in _PREFIXES:
            for nfields in range(1, _MAX_FIELDS + 1):
                entries = _chain_from(data, name_at, kind, nfields)
                if len(entries) < min_entries:
                    continue
                if best is None or len(entries) > len(best["entries"]):
                    best = {"entries": entries, "prefix": kind,
                            "fields": nfields, "offset": entries[0]["offset"]}
        if best and len(best["entries"]) > 50:
            break                      # a chain this long is the answer
        seen_starts.add(name_at)
    if best is None:
        return None
    # Confidence from the chain length AND from whether the names read like
    # names. Length alone said 0.84 about a wall texture: a long chain only
    # proves the layout is self-consistent, and smooth bytes are extremely
    # self-consistent. Two independent things have to hold.
    #
    # Capped below a signature match on purpose: this is a shape, not a magic
    # number, and it must never outrank something that verified one.
    n = len(best["entries"])
    names = [e["name"].encode("latin-1", "replace")
             if isinstance(e["name"], str) else e["name"]
             for e in best["entries"]]
    quality = sum(_alnum_ratio(nm) for nm in names) / float(len(names))
    # A table of real paths shares structure between siblings -- an extension
    # set, a directory prefix. One that does not is a weaker hypothesis even if
    # every entry passed on its own.
    exts = {nm.rsplit(b".", 1)[-1].lower() for nm in names if b"." in nm}
    shared = len(exts) <= max(3, len(names) // 8)
    length_term = min(0.85, 0.4 + n / 200.0)
    conf = length_term * quality * (1.0 if shared else 0.75)
    best["confidence"] = round(min(0.85, conf), 2)
    return best


# extension -> the magic its payload should open with. Only used to VERIFY a
# placement hypothesis, never to identify anything, so a short list of things
# acidcat already knows is enough.
def _ext_magics():
    from acidcat.core.infra import sniff
    out = {}
    for fmt, (magic, _kind) in sniff.AUDIO_CONTAINERS.items():
        out.setdefault("." + fmt, magic)
    out.update({".ogg": b"OggS", ".oga": b"OggS", ".mp3": b"ID3",
                ".wav": b"RIFF", ".flac": b"fLaC", ".aiff": b"FORM",
                ".aif": b"FORM", ".mid": b"MThd"})
    return out


def place_entries(fh, toc, data_start=None):
    """Give each table entry a byte offset, and check whether that is real.

    The table says how big things are; it does not always say where they are.
    But an archive that writes an index followed by its payloads back to back
    -- which is most of them -- makes the offsets derivable: start where the
    table ended and accumulate.

    Which integer field is the stored size is not knowable in advance, so every
    field is tried and the answer is the one that VERIFIES: entries whose names
    carry a known extension must land on that format's magic. A wrong field
    scores near zero immediately, a right one scores near 1.0, and the score is
    reported rather than assumed. On the archive this was built against, field 1
    placed 64 of 64 .ogg entries exactly on OggS while field 0 placed none.

    Returns (entries, field_index, verified, checked) with `offset`/`length`
    added to each entry, or (entries, None, 0, 0) when nothing verifies.
    """
    entries = toc["entries"]
    if not entries:
        return entries, None, 0, 0
    base = entries[-1]["end"] if data_start is None else data_start
    magics = _ext_magics()

    best = (None, 0, 0)
    for k in range(toc["fields"]):
        off, verified, checked = base, 0, 0
        for e in entries:
            length = e["fields"][k]
            if length < 0:
                verified, checked = 0, 0
                break
            ext = ("." + e["name"].rsplit(".", 1)[-1].lower()
                   if "." in e["name"] else "")
            want = magics.get(ext)
            if want:
                checked += 1
                try:
                    fh.seek(off)
                    if fh.read(len(want)) == want:
                        verified += 1
                except OSError:
                    pass
            off += length
        if checked and verified > best[1]:
            best = (k, verified, checked)

    k, verified, checked = best
    if k is None:
        return entries, None, 0, 0
    off = base
    for e in entries:
        e["offset"] = off
        e["length"] = e["fields"][k]
        off += e["length"]
    return entries, k, verified, checked
