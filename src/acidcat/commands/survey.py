"""
acidcat survey -- count chunk IDs across a directory tree.
"""

import csv
import os
import sys
from collections import Counter, defaultdict

from acidcat.core.formats.riff import iter_chunks
from acidcat.commands._output import add_output_format_arg
from acidcat.core.infra.render import output
from acidcat.util.csv_helpers import safe_basename_for_csv


def register(subparsers):
    p = subparsers.add_parser("survey", help="Count RIFF chunk types across a directory.")
    p.add_argument("target", help="Directory to scan.")
    p.add_argument("-n", "--num", type=int, default=1000000, help="Max files to scan.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--has", help="Only count files containing these chunk IDs (comma-separated).")
    p.add_argument("--examples", type=int, default=1,
                   help="Example file paths to store per chunk ID.")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.add_argument("-o", "--output", help="Write output to file.")
    p.set_defaults(func=run)


def run(args):
    directory = args.target
    if not os.path.isdir(directory):
        print(f"acidcat survey: {directory}: Not a directory", file=sys.stderr)
        return 2

    wanted = None
    has_val = getattr(args, 'has', None)
    if has_val:
        wanted = set(w.strip().upper() for w in has_val.split(",") if w.strip())

    quiet = getattr(args, 'quiet', False)
    num = getattr(args, 'num', 1000000)
    max_examples = getattr(args, 'examples', 1)

    counts = Counter()
    examples = defaultdict(list)
    files_scanned = 0
    # a .wav the chunk walker cannot read is not "not a WAV" -- for a specimen
    # hunter it is the interesting case, and dropping it from the denominator
    # made a directory of broken files report "no RIFF/WAV files found"
    unparseable = 0
    capped = False

    for root, _, files in os.walk(directory):
        for fn in files:
            if not fn.lower().endswith(".wav"):
                continue
            path = os.path.join(root, fn)
            ids = []
            try:
                for cid, _, _ in iter_chunks(path):
                    ids.append(cid)
            except Exception:
                unparseable += 1
                continue

            if not ids:
                unparseable += 1
                continue

            if wanted:
                u = {c.upper() for c in ids}
                if not (u & wanted):
                    continue

            seen_local = set()
            for c in ids:
                if c not in seen_local:
                    seen_local.add(c)
                    counts[c] += 1
                    if len(examples[c]) < max_examples:
                        examples[c].append(path)

            files_scanned += 1
            if not quiet and files_scanned % 200 == 0:
                print(f"  [survey] {files_scanned} files...", file=sys.stderr)
            if files_scanned >= num:
                capped = True
                break
        if files_scanned >= num:
            break

    # Format results
    rows = []
    for cid, cnt in counts.most_common():
        rows.append({
            "chunk_id": cid,
            "files": cnt,
            "example": examples[cid][0] if examples[cid] else "",
        })

    fmt_name = getattr(args, 'output_format', 'table')
    stream = sys.stdout
    out_path = getattr(args, 'output', None)
    if out_path:
        stream = open(out_path, 'w', encoding='utf-8')

    if fmt_name == "table":
        note = f", {unparseable} unparseable" if unparseable else ""
        if capped:
            note += f" (stopped at the -n {num} cap -- more files remain)"
        stream.write(f"Chunk ID Survey -- {files_scanned} WAV files scanned"
                     f"{note}\n\n")
        if files_scanned == 0:
            if unparseable:
                # "none found" and "found, none readable" are different answers,
                # and for a specimen hunter the second one is the interesting one
                stream.write(f"  ({unparseable} .wav file(s) found, none readable "
                             f"as RIFF -- try: acidcat classify DIR, or "
                             f"acidcat inspect --resync FILE)\n")
            else:
                stream.write("  (no RIFF/WAV files found -- survey only "
                             "processes .wav files)\n")
        for r in rows:
            stream.write(f"  {r['chunk_id']:6s} : {r['files']} files\n")
    else:
        output(rows, fmt=fmt_name, stream=stream)

    if stream is not sys.stdout:
        stream.close()
    elif not quiet:
        tail = f", {unparseable} unparseable" if unparseable else ""
        if capped:
            tail += f", stopped at the -n {num} cap"
        print(f"\n[INFO] Scanned {files_scanned} WAV file(s), {len(counts)} "
              f"unique chunk ID(s){tail}.", file=sys.stderr)

    # a tree with no parseable audio in it is a negative result
    return 0 if files_scanned else 1
