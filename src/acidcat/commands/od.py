"""acidcat od -- objdump-x-style annotated, colored hex dump of a file's structure.

Lays out the parsed bytes as a hex dump -- offset, hex, meaning -- with each
chunk/block header and each decoded field on its own line, the field's byte-run
colored (cycling palette) and labeled with its name/value/note, so the structure
is legible directly in the raw byte stream. Bulk audio payload is elided.

Like od(1), it never refuses to show you bytes. A format it can walk gets the
annotated layout; anything else -- a proprietary container, a carved region, a
raw dump off a device -- falls back to a plain hex dump. Structure is a bonus,
not a precondition.

    acidcat od song.wav                       # annotated, by chunk and field
    acidcat od unknown.bin                    # plain hex, no walker needed
    acidcat od blob.img --offset 0x5d1000 --length 2048
    acidcat od blob.img --at find:RIFF --length 64
    acidcat locate blob.img --json | acidcat od blob.img --region 0

Complements `inspect --hex` (a value-first table); this is a bytes-first layout.
"""

import sys
from acidcat.util.color import add_color_arg, color_enabled

from acidcat.core.infra import bytefields as bf
from acidcat.core.infra.mapped import map_file
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

# cycling foreground colors so adjacent fields are visually distinct
_FIELD_COLORS = (36, 32, 33, 35, 34, 31, 96, 92, 93, 95)


def register(subparsers):
    p = subparsers.add_parser(
        "od", help="objdump-x-style annotated, colored hex dump of a file's structure")
    p.add_argument("target", help="File to dump, or '-' for stdin.")
    add_color_arg(p)
    p.add_argument("--width", type=int, default=16, metavar="N",
                   help="hex bytes per line, and per field before eliding "
                        "(default 16)")
    # range selection -- same vocabulary as `carve`, so a region found by
    # `locate` can be handed to either verb unchanged
    p.add_argument("--offset", metavar="N",
                   help="Start at this offset (0x.. or decimal). Forces the raw "
                        "dump: a byte range has no chunk structure of its own.")
    p.add_argument("--at", metavar="EXPR",
                   help="Anchored start: 0xNN | end[-N] | find:STR|0xHEX[+N] | "
                        "chunk:ID[+N].")
    p.add_argument("--length", metavar="N",
                   help="Number of bytes to dump from the start.")
    p.add_argument("--end", metavar="N",
                   help="End offset (exclusive), instead of --length.")
    p.add_argument("--region", type=int, metavar="N",
                   help="Dump the Nth region reported by `locate` (0-based); "
                        "runs locate itself, so no piping is required.")
    p.set_defaults(func=run)




def _c(code, text, on):
    return f"\033[{code}m{text}\033[0m" if on else text


def _hexcells(b):
    return " ".join(f"{x:02x}" for x in b)


def _ascii(b):
    return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


def run(args):
    from acidcat.util.stdin import resolved_input
    with resolved_input(args.target) as _p:
        if _p is None:
            print("acidcat od: no data on stdin", file=sys.stderr)
            return 1
        args.target = _p
        return _run(args)


def _size(path):
    import os
    return os.path.getsize(path)


def _int(text, what):
    try:
        return int(text, 0)
    except (ValueError, TypeError):
        raise ValueError(f"{what}: not an offset/length: {text!r}")


def _requested_range(args, path, size):
    """(start, length) from --offset/--at/--length/--end/--region, or None when
    the whole file was asked for. Raises ValueError on a bad expression.

    Read through getattr so a programmatic caller (the TUI, a test, another
    command) can pass a namespace carrying only the flags it cares about.
    """
    region = getattr(args, "region", None)
    at = getattr(args, "at", None)
    offset = getattr(args, "offset", None)
    length_s = getattr(args, "length", None)
    end_s = getattr(args, "end", None)
    if region is not None:
        from acidcat.core.forensics import locate as locatemod
        with open(path, "rb") as f:
            recs = locatemod.locate(f.read(), mode="normal")
        if not recs:
            raise ValueError("no regions located in this file")
        if not 0 <= region < len(recs):
            raise ValueError(f"--region {region} out of range "
                             f"(locate found {len(recs)}: 0..{len(recs) - 1})")
        r = recs[region]
        return r["offset"], r["length"]

    start = None
    if at is not None:
        start = bf.resolve_offset(at, path, size)
    elif offset is not None:
        start = _int(offset, "--offset")
    if start is None and length_s is None and end_s is None:
        return None
    start = start or 0
    if end_s is not None:
        length = max(0, _int(end_s, "--end") - start)
    elif length_s is not None:
        length = _int(length_s, "--length")
    else:
        length = size - start
    return start, length


def _raw_dump(data, start, length, width, on, note):
    """A plain hex dump of a byte range -- what od(1) does. Used when the file
    has no walker, or when an explicit range was asked for."""
    end = min(start + length, len(data))
    print(_c("1", note, on))
    for base in range(start, end, width):
        row = data[base:min(base + width, end)]
        print(f"  0x{base:08x}  {_hexcells(row):<{width * 3}} {_ascii(row)}")
    return 0


def _run(args):
    path = args.target
    on = color_enabled(args)
    try:
        rng = _requested_range(args, path, _size(path))
    except (ValueError, KeyError, OSError) as e:
        print(f"acidcat od: {path}: {e}", file=sys.stderr)
        return 2

    # an explicit byte range has no structure of its own -- dump it plainly
    if rng is not None:
        start, length = rng
        data, close = map_file(path)
        try:
            return _raw_dump(data, start, length, args.width, on,
                             f"{path}  0x{start:08x} .. 0x{start + length:08x}"
                             f"  ({length:,} bytes)")
        finally:
            close()

    try:
        label, chunks, warns = walk_file(path)
    except Unsupported:
        # od never refuses to show bytes: no walker means a plain dump, not an
        # error. `inspect` is where "I do not know this format" is the answer.
        data, close = map_file(path)
        try:
            return _raw_dump(data, 0, len(data), args.width, on,
                             f"{path}  {len(data):,} bytes  "
                             f"(no structural walker -- raw dump)")
        finally:
            close()
    # mmap, not f.read(): od only slices small header/field/preview runs, and
    # a mapped file serves those without loading multi-GB payloads into RAM
    data, close = map_file(path)
    try:
        def dim(t):
            return _c("2", t, on)

        header = _c("1", f"{label}  {len(data):,} bytes  {len(chunks)} chunks", on)
        if warns:
            header += dim(f"   {len(warns)} warning(s)")
        print(header)

        for c in chunks:
            base = c.get("payload_base", c["offset"] + 8)
            summary = c.get("summary", "")
            title = _c("1;37", f"{str(c['id'])!r} @ 0x{c['offset']:08x}  {c['size']:,} bytes", on)
            print("\n" + title + (dim("  " + summary) if summary else ""))

            # the chunk/block header bytes (id + size), dimmed
            hdr = data[c["offset"]:base]
            c_off = f"0x{c['offset']:08x}"
            print(f"  {dim(c_off)}  {dim(_hexcells(hdr))}  {dim(_ascii(hdr))}")

            fields = c.get("fields", [])
            for i, fl in enumerate(fields):
                off = fl.get("off")
                name = _c("1", fl["name"], on)
                value = fl.get("value")
                note = dim("  " + fl["note"]) if fl.get("note") else ""
                if off is None:                   # derived / synthetic field
                    print(f"  {'':10}  {dim('(derived)')}  {name} = {value}{note}")
                    continue
                abs_off = base + off
                avail = max(0, min(abs_off + fl.get("len", 0), len(data)) - abs_off)
                # copy only the rendered prefix out of the map; a multi-MB
                # field must not be materialized to show its first bytes
                fb = data[abs_off:abs_off + min(avail, args.width)]
                cells = _c(_FIELD_COLORS[i % len(_FIELD_COLORS)], _hexcells(fb), on)
                more = dim(f" +{avail - args.width}") if avail > args.width else ""
                print(f"  0x{abs_off:08x}  {cells}{more}  {name} = {value}{note}")

            # opaque chunk (no decoded fields): show the first row, elide the rest
            if not fields and c["size"] > 0:
                preview = data[base:base + args.width]
                elided = dim(f"({c['size']:,} bytes payload)")
                print(f"  0x{base:08x}  {dim(_hexcells(preview))}  {dim(_ascii(preview))}  {elided}")
        return 0
    finally:
        close()
