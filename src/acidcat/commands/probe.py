"""acidcat probe -- low-level byte dissection (the RE-tool surface).

Read a file as raw bytes the way a reverse engineer does, with the addresses
resolved through acidcat's format walker so you can name structure instead of
counting bytes.

    acidcat probe FILE read AT [--type u32] [--count N] [--be|--le]
    acidcat probe FILE scan VALUE [--type u32]
    acidcat probe FILE find HEX
    acidcat probe FILE strings [--min N]
    acidcat probe FILE hexdump AT [--len N]
    acidcat probe FILE diff OTHER

AT is a raw offset (0x2c / 44) OR a structural name: a chunk id (data), or a
chunk field (fmt.sample_rate). VALUE for scan is an integer (or a float for
f32/f64); it is searched in both byte orders. HEX for find is a hex string
(64617461) or, with a leading s:, literal text (s:data).
"""

import json
import os
import sys
from acidcat.util.stdin import display_name
from acidcat.util.color import add_color_arg, color_enabled

from acidcat.core import probe as pr
from acidcat.core.forensics import viz
from acidcat.core.infra.mapped import map_file
from acidcat.tui_theme import BYTE_CLASS


def register(subparsers):
    p = subparsers.add_parser(
        "probe",
        help="Byte-level dissection: typed read, value scan, find, strings, hexdump, diff.")
    p.add_argument("file", help="File to dissect, or '-' for stdin.")
    # probe is the RE surface and had no machine output at all: every subverb
    # printed its summary and its results together on stdout, so scripting
    # `probe find` meant `tail -n +2 | tr -d ' '`. Declared on the parent so it
    # applies to whichever subverb follows.
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="Emit results as JSON on stdout (read/scan/find/"
                        "strings/diff/entropy). The human summary moves to "
                        "stderr, so the data pipes cleanly either way.")
    sub = p.add_subparsers(dest="verb", metavar="VERB")

    # The gap between acidcat-as-hex-viewer and acidcat-as-RE-workbench. You
    # could derive a container's offset table with `probe read` and then had no
    # way to act on it: --struct decodes one fixed record and cannot take a
    # count from a field it just read, so carving the regions meant leaving the
    # tool and computing offsets in Python. This emits `locate`-shaped records,
    # so the thing you just learned feeds `carve --batch -` unchanged.
    tb = sub.add_parser(
        "table", help="Walk an offset table into carve-ready regions.")
    tb.add_argument("at", help="Where the table starts (any --at expression).")
    tb.add_argument("--type", "-t", default="u32", choices=sorted(pr.FMT_STRUCT),
                    help="Entry type (default u32).")
    tb.add_argument("--count", "-n", type=int,
                    help="Number of entries, if you know it.")
    tb.add_argument("--count-at", metavar="EXPR",
                    help="Read the entry count from the file at EXPR instead.")
    tb.add_argument("--count-type", default="u32", choices=sorted(pr.FMT_STRUCT),
                    help="Type of the --count-at value (default u32).")
    tb.add_argument("--base", metavar="EXPR", default=None,
                    help="What the entries are relative to. An offset "
                         "expression, or 'after-table' for the byte just past "
                         "the table itself (the common layout). Default: the "
                         "entries are absolute file offsets.")
    tb.add_argument("--end", metavar="EXPR",
                    help="Where the last region ends (default: EOF).")
    tb.add_argument("--be", action="store_true", help="Force big-endian.")
    tb.add_argument("--le", action="store_true", help="Force little-endian.")

    r = sub.add_parser("read", help="Read AT as typed values (pwndbg x).")
    r.add_argument("at", help="Offset (0x.. / decimal) or name (chunk / chunk.field).")
    r.add_argument("--type", "-t", default="u32", choices=sorted(pr.FMT_STRUCT),
                   help="Value type (default u32).")
    r.add_argument("--count", "-n", type=int, default=1, help="How many values.")
    r.add_argument("--be", action="store_true", help="Force big-endian.")
    r.add_argument("--le", action="store_true", help="Force little-endian.")

    s = sub.add_parser("scan", help="Find every offset holding VALUE (Cheat Engine).")
    s.add_argument("value", help="The value to find (int, or float for f32/f64).")
    s.add_argument("--type", "-t", default="u32", choices=sorted(pr.FMT_STRUCT),
                   help="How to encode VALUE (default u32).")

    f = sub.add_parser("find", help="Find every offset of a byte pattern.")
    f.add_argument("pattern", help="Hex bytes (64617461) or s:text for literal ASCII.")

    st = sub.add_parser("strings", help="Printable ASCII runs with offsets.")
    st.add_argument("--min", "-m", type=int, default=4, help="Minimum run length.")

    h = sub.add_parser("hexdump", help="Annotated hexdump at AT.")
    h.add_argument("at", help="Offset or structural name.")
    h.add_argument("--len", "-l", dest="length", type=int, default=256,
                   help="Bytes to dump (default 256, or the chunk size for a name).")

    d = sub.add_parser("diff", help="Changed byte ranges vs another file.")
    d.add_argument("other", help="The file to compare against.")

    en = sub.add_parser("entropy",
                        help="Shannon entropy curve + byte histogram (spot encrypted/compressed spans).")
    en.add_argument("--width", "-w", type=int, default=72, help="Plot width in cells.")

    mp = sub.add_parser("map",
                        help="Hilbert byte-class map (binvis): the file's shape at a glance.")
    # no -o short form: -o is "output file" everywhere else in acidcat
    mp.add_argument("--order", type=int, default=5,
                    help="Grid is 2^order per side (default 5 = 32x32).")
    add_color_arg(mp, deprecated_no_color=True)

    p.set_defaults(func=run)


def _rgb(hexc):
    return int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)




def _byteorder(args, label):
    if getattr(args, "be", False):
        return "big"
    if getattr(args, "le", False):
        return "little"
    return pr.default_byteorder(label)


def _emit(args, payload):
    """JSON to stdout for the machine path. Returns True if it handled output."""
    if not getattr(args, "as_json", False):
        return False
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return True


def run(args):
    from acidcat.util.stdin import resolved_input
    with resolved_input(args.file) as _p:
        if _p is None:
            print("acidcat probe: no data on stdin", file=sys.stderr)
            return 1
        args.file = _p
        return _run(args)


def _run(args):
    verb = getattr(args, "verb", None)
    if not verb:
        print("acidcat probe: pick a verb (read/scan/find/strings/hexdump/diff)",
              file=sys.stderr)
        return 2
    path = args.file
    try:
        # mmap, not f.read(): every verb is random access (slice, find,
        # unpack_from), which a mapped file serves with OS paging -- a large
        # or crafted input no longer costs its full size in RAM
        data, close = map_file(path)
    except OSError as e:
        print(f"acidcat probe: {path}: {e}", file=sys.stderr)
        return 2
    try:
        return _dispatch(args, verb, path, data)
    finally:
        close()


def _table_regions(args, path, data, order):
    """Offset table -> [{offset, length, kind}] in `locate` record shape.

    Entries are region starts; each region runs to the next entry, and the last
    to --end or EOF. Returns (records, meta) or raises ValueError with a reason
    the caller can print.
    """
    start, _ln, _note = pr.resolve(path, args.at)
    size = pr.FMT_STRUCT[args.type][1] if args.type != "u24" else 3

    count = args.count
    if args.count_at is not None:
        coff, _l, _n = pr.resolve(path, args.count_at)
        vals = pr.read_typed(data, coff, args.count_type, 1, order)
        if not vals:
            raise ValueError(f"could not read a count at {args.count_at}")
        count = int(vals[0])
    if count is None:
        raise ValueError("give --count N or --count-at EXPR")
    # a count read from the file is attacker-controlled by definition: it is the
    # value under investigation. Bound it by what the file can actually hold
    # rather than trusting it into a multi-GB allocation.
    room = max(0, (len(data) - start) // size)
    if count < 1:
        raise ValueError(f"entry count is {count}")
    declared = count                     # before clamping: what the FILE claimed
    truncated = count > room
    if truncated:
        count = room

    entries = pr.read_typed(data, start, args.type, count, order)
    if not entries:
        raise ValueError(f"no entries readable at 0x{start:x}")

    if args.base == "after-table":
        base = start + len(entries) * size
    elif args.base is not None:
        base, _l, _n = pr.resolve(path, args.base)
    else:
        base = 0
    if args.end is not None:
        end_at, _l, _n = pr.resolve(path, args.end)
    else:
        end_at = len(data)

    starts = [base + int(e) for e in entries]
    recs = []
    for i, off in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else end_at
        if off < 0 or off > len(data) or nxt <= off:
            continue                     # a lying entry drops out, not the run
        recs.append({"offset": off, "length": min(nxt, len(data)) - off,
                     "kind": "entry", "index": i})
    meta = {"table_at": start, "entries": len(entries), "base": base,
            "declared_count": declared,
            "truncated_to_file": truncated, "usable": len(recs)}
    return recs, meta


def _dispatch(args, verb, path, data):
    label, _chunks, _warns = pr._walk(path)

    if verb == "table":
        order = "big" if args.be else ("little" if args.le
                                       else _byteorder(args, label))
        try:
            recs, meta = _table_regions(args, path, data, order)
        except (KeyError, ValueError) as e:
            print(f"acidcat probe: {e}", file=sys.stderr)
            return 2
        if _emit(args, {"verb": "table", **meta, "regions": recs}):
            return 0 if recs else 1
        if meta["truncated_to_file"]:
            print(f"  count {meta['declared_count']} exceeds what the file "
                  f"holds; walked {meta['entries']}", file=sys.stderr)
        print(f"{meta['entries']} entr(ies) at 0x{meta['table_at']:08x}, "
              f"base 0x{meta['base']:08x} -> {len(recs)} region(s)",
              file=sys.stderr)
        for r in recs:
            print(f"  [{r['index']:>4}]  0x{r['offset']:08x}  {r['length']:>12,}")
        return 0 if recs else 1

    if verb == "read":
        try:
            off, _ln, note = pr.resolve(path, args.at)
        except (KeyError, ValueError) as e:
            print(f"acidcat probe: {e}", file=sys.stderr)
            return 2
        order = _byteorder(args, label)
        vals = pr.read_typed(data, off, args.type, args.count, order)
        if not vals:
            print(f"acidcat probe: nothing to read at 0x{off:x}", file=sys.stderr)
            return 1
        if _emit(args, {"verb": "read", "offset": off, "type": args.type,
                        "endian": order, "anchor": note,
                        "values": list(vals)}):
            return 0
        head = f"0x{off:08x}  {args.type} {order}-endian  ({note})"
        print(head)
        for i, v in enumerate(vals):
            print(f"  [{i}] {v}")
        return 0

    if verb == "scan":
        try:
            value = float(args.value) if args.type in ("f32", "f64") else pr.parse_int(args.value)
        except ValueError:
            print(f"acidcat probe: bad value {args.value!r}", file=sys.stderr)
            return 2
        hits = pr.scan_value(data, value, args.type)
        if _emit(args, {"verb": "scan", "value": args.value, "type": args.type,
                        "hits": [{"offset": o, "endian": e} for o, e in hits]}):
            return 0 if hits else 1
        print(f"{len(hits)} hit(s) for {args.value} as {args.type}",
              file=sys.stderr)
        for off, order in hits:
            print(f"  0x{off:08x}  ({order})")
        return 0 if hits else 1

    if verb == "find":
        pat = args.pattern
        if pat.startswith("s:"):
            needle = pat[2:].encode("latin-1")
        else:
            try:
                needle = bytes.fromhex(pat)
            except ValueError:
                print(f"acidcat probe: bad hex {pat!r} (use s: for text)", file=sys.stderr)
                return 2
        offs = pr.find_bytes(data, needle)
        if _emit(args, {"verb": "find", "pattern": pat,
                        "length": len(needle),
                        "hits": [{"offset": o} for o in offs]}):
            return 0 if offs else 1
        print(f"{len(offs)} hit(s) for {pat}", file=sys.stderr)
        for off in offs:
            print(f"  0x{off:08x}")
        return 0 if offs else 1

    if verb == "strings":
        # memoryview: byte-wise iteration must yield ints as bytes does
        # (iterating the mmap itself yields 1-byte bytes objects)
        with memoryview(data) as view:
            found = pr.strings(view, args.min)
        if _emit(args, {"verb": "strings", "min_length": args.min,
                        "strings": [{"offset": o, "text": t}
                                    for o, t in found]}):
            return 0 if found else 1
        for off, text in found:
            print(f"0x{off:08x}  {text}")
        return 0 if found else 1

    if verb == "hexdump":
        try:
            off, ln, _note = pr.resolve(path, args.at)
        except (KeyError, ValueError) as e:
            print(f"acidcat probe: {e}", file=sys.stderr)
            return 2
        length = args.length if args.length != 256 else (ln or 256)
        print(pr.hexdump(data, off, length))
        return 0

    if verb == "diff":
        try:
            other, oclose = map_file(args.other)
        except OSError as e:
            print(f"acidcat probe: {args.other}: {e}", file=sys.stderr)
            return 2
        try:
            ranges, la, lb = pr.diff(data, other)
        finally:
            oclose()
        if _emit(args, {"verb": "diff", "a": display_name(path),
                        "b": os.path.basename(args.other),
                        "a_length": la, "b_length": lb,
                        "identical": not ranges and la == lb,
                        "ranges": [{"offset": st, "end": en, "length": en - st}
                                   for st, en in ranges]}):
            return 0 if (not ranges and la == lb) else 1
        if not ranges and la == lb:
            print("identical")
            return 0
        print(f"{display_name(path)} ({la:,}) vs {os.path.basename(args.other)} "
              f"({lb:,}): {len(ranges)} changed range(s)")
        for s, e in ranges:
            print(f"  0x{s:08x}..0x{e:08x}  ({e - s} bytes)")
        if la != lb:
            print(f"  lengths differ by {abs(la - lb):,} bytes")
        return 0

    if verb == "entropy":
        # memoryview: viz iterates bytes as ints, and view slices are
        # zero-copy windows into the map
        with memoryview(data) as view:
            ent = viz.windowed_entropy(view, max(8, args.width))
            if _emit(args, {"verb": "entropy", "file": display_name(path),
                            "size": len(data), "window": max(8, args.width),
                            "min": min(ent), "max": max(ent),
                            "mean": sum(ent) / len(ent),
                            "high_windows": sum(1 for e in ent if e >= 7.2),
                            "high_threshold": 7.2,
                            "windows": [round(e, 4) for e in ent]}):
                return 0
            print(f"entropy  {display_name(path)}  {len(data):,} bytes  (0 = uniform .. 8 = random)")
            for line in viz.braille_line(ent, width=args.width, height=8, vmin=0, vmax=8):
                print("  " + line)
            hi = sum(1 for e in ent if e >= 7.2)
            summary = (f"  min {min(ent):.2f}  max {max(ent):.2f}  "
                       f"mean {sum(ent) / len(ent):.2f} bits/byte")
            if hi:
                summary += f"   [{hi} window(s) >= 7.2: encrypted or compressed]"
            print(summary)
            print("  byte distribution:")
            for line in viz.byte_histogram(view, width=128, height=5):
                print("  " + line)
        return 0

    if verb == "map":
        # memoryview for the same int-iteration reason as entropy/strings
        with memoryview(data) as view:
            grid, side = viz.hilbert_grid(view, args.order)
        color = color_enabled(args)
        print(f"byte map  {display_name(path)}  {len(data):,} bytes  "
              f"({side}x{side} Hilbert; adjacent cells are adjacent bytes)")
        for row in grid:
            cells = []
            for b in row:
                glyph, cls = viz.byte_class(b)
                hexc = BYTE_CLASS[cls]
                if color:
                    r, g, bl = _rgb(hexc)
                    cells.append(f"\x1b[38;2;{r};{g};{bl}m█\x1b[0m")
                else:
                    cells.append(glyph)
            print("  " + "".join(cells))
        print("  legend:  . null   o ascii   - control   + high   # 0xFF")
        return 0

    return 2
