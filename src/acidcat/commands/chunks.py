"""
acidcat chunks -- walk RIFF chunks in a file, showing offsets and parsed fields.
"""

import os
import sys
from acidcat.util.stdin import display_name

from acidcat.core.formats.riff import iter_chunks, get_riff_info
from acidcat.commands._output import add_output_format_arg, out_stream
from acidcat.core.infra.render import output


def register(subparsers):
    p = subparsers.add_parser("chunks", help="Walk RIFF chunks in a WAV file.")
    p.add_argument("target", help="Path to a WAV file, or '-' for stdin.")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.add_argument("-o", "--output", help="Write output to file.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Diagnostic lines on stderr (container size, walk summary).")
    p.set_defaults(func=run)


def _vlog(args, msg):
    if getattr(args, "verbose", False) and not getattr(args, "quiet", False):
        print(msg, file=sys.stderr)


def run(args):
    from acidcat.util.stdin import resolved_input
    with resolved_input(args.target) as _p:
        if _p is None:
            print("acidcat chunks: no data on stdin", file=sys.stderr)
            return 1
        args.target = _p
        return _run(args)


def _run(args):
    filepath = args.target
    if os.path.isdir(filepath):
        print(f"acidcat chunks: {filepath}: is a directory (expected a file)", file=sys.stderr)
        return 2
    if not os.path.isfile(filepath):
        print(f"acidcat chunks: {filepath}: No such file", file=sys.stderr)
        return 2

    fmt_name = getattr(args, 'output_format', 'table')

    _vlog(args, f"[chunks] file={display_name(filepath)} "
                f"size={os.path.getsize(filepath)}")

    # Get RIFF container info
    riff_info = get_riff_info(filepath)
    if riff_info is None:
        print(f"acidcat chunks: {filepath}: Not a RIFF file", file=sys.stderr)
        return 1

    # Walk raw chunks (offsets + sizes)
    chunk_list = []
    for cid, offset, size in iter_chunks(filepath):
        chunk_list.append({
            "chunk": cid,
            "offset": offset,
            "size": size,
        })
    _vlog(args, f"[chunks] walked {len(chunk_list)} chunks in "
                f"RIFF {riff_info['type']}")

    # Parsed fields come from the inspect walker (the single decoder since
    # the legacy parse_riff retirement); a RIFF form the walkers do not
    # know degrades to the raw layout above.
    from acidcat.core.walk import walk_file, Unsupported
    results = []
    try:
        _label, walked, _fwarns = walk_file(filepath)
    except Unsupported:
        walked = []
    for c in walked:
        for fld in c.get("fields", []):
            results.append((c["id"], fld["name"], fld["value"]))
    _vlog(args, f"[chunks] parsed {len(results)} fields from "
                f"{len(walked)} chunks")

    with out_stream(getattr(args, 'output', None)) as stream:
        if fmt_name == "table":
            stream.write(f"RIFF container: {riff_info['size']} bytes, "
                         f"type={riff_info['type']}\n")
            stream.write(f"File: {display_name(filepath)}\n\n")

            # Raw chunk layout
            stream.write("Chunk Layout:\n")
            for c in chunk_list:
                stream.write(f"  {c['chunk']:4s}  @ {c['offset']:>8d}  "
                             f"size={c['size']}\n")

            # Parsed fields
            if results:
                stream.write(f"\nParsed Fields:\n")
                for cid, key, val in results:
                    stream.write(f"  {cid}.{key} = {val}\n")
        else:
            # JSON or CSV: emit the parsed fields
            data = [{"chunk": cid, "key": key, "value": val}
                    for cid, key, val in results]
            output(data, fmt=fmt_name, stream=stream)

    return 0
