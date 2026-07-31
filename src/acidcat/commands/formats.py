"""acidcat formats -- the capability matrix: what acidcat can do with each format.

Format support is spread across several dispatch tables (sniff identifies, walk/
inspects, samples extracts, convert decodes, repair fixes), and no single place
answered "what does acidcat do with format X". This command is that map: it reads
the live tables and prints one row per format with a tick under each capability.

    acidcat formats                 # the whole matrix
    acidcat formats sf2             # just one format's row
    acidcat formats -f json         # machine-readable, for piping

Inspect and Extract are read straight from their registries (walk._WALKERS and
samples.EXTRACTABLE), so they never drift. Convert and Repair dispatch on magic
bytes rather than a format table, so their columns come from the hand-listed sets
below -- but the tests pin those sets against the live dispatch (probing a real
magic sample per format), so a stale entry fails the suite. Turning convert/repair
into real format tables is the next housekeeping step.
"""

import argparse
import json
import sys

from acidcat.commands._output import add_output_format_arg

# Convert and Repair have no format-keyed registry to read (they branch on magic
# bytes in commands/convert.py and core/constraints.py), so these are listed here
# rather than derived. test_formats.py pins each against the live dispatch by
# probing a real magic sample, so a set that falls out of sync fails the suite.
_CONVERT = {"ncw", "8svx", "sf2", "bitwig", "wav"}          # commands/convert.py run()
# every RIFF/FORM container (IffRepairer.applies == structure.is_iff) plus flac and
# mp4 -- constraints._repairers(). Not just the WAVE/AIFF subset it looks like.
_REPAIR = {"wav", "rf64", "aiff", "aifc", "sf2", "8svx", "smus", "rmid", "akp",
           "e4b", "e5b", "flac", "mp4"}

# labels for formats that extract/convert but have no inspect walker (so no label
# in walk._WALKERS). Keeps every row named.
_EXTRA_LABELS = {
    "vag": "PS1 SPU-ADPCM sample", "hps": "HAL PCM Stream (GameCube)",
    "adx": "CRI ADX (GameCube/arcade)", "brstm": "Nintendo BRSTM stream (GC/Wii)",
    "cdxa": "CD-XA sector image (PS1)", "gcm": "GameCube disc image",
    "cue": "CUE sheet (CD-DA)", "wii": "Wii disc image",
    "n64rom": "Nintendo 64 ROM", "snesrom": "SNES ROM",
}


def register(subparsers):
    p = subparsers.add_parser(
        "formats", help="Show the capability matrix: inspect/extract/convert/repair "
                        "support per format.")
    p.add_argument("format", nargs="?",
                   help="Show just this format id (as sniff/inspect report it).")
    add_output_format_arg(p, only=("table", "json", "tsv"), deprecated_f=False)
    p.add_argument("--format-out", dest="output_format",
                   choices=("table", "json", "tsv"),
                   help=argparse.SUPPRESS)          # deprecated: use --output-format
    p.set_defaults(func=run)


def _matrix():
    """Build [{id, label, inspect, extract, convert, repair}] over every format
    with any capability, read from the live registries."""
    from acidcat.core.extract import samples
    from acidcat.core.walk import _WALKERS

    labels = {fid: lbl for fid, (lbl, _fn) in _WALKERS.items()}
    labels.update({fid: _EXTRA_LABELS.get(fid, fid) for fid in samples.EXTRACTABLE
                   if fid not in labels})
    ids = set(_WALKERS) | set(samples.EXTRACTABLE) | _CONVERT | _REPAIR
    rows = []
    for fid in sorted(ids):
        rows.append({
            "id": fid,
            "label": labels.get(fid, _EXTRA_LABELS.get(fid, fid)),
            "inspect": fid in _WALKERS,
            "extract": fid in samples.EXTRACTABLE,
            "convert": fid in _CONVERT,
            "repair": fid in _REPAIR,
        })
    return rows


_CAPS = ("inspect", "extract", "convert", "repair")


def _print_table(rows):
    wid = max((len(r["id"]) for r in rows), default=2)
    lwid = max((len(r["label"]) for r in rows), default=5)
    head = f"{'FORMAT':<{wid}}  {'DESCRIPTION':<{lwid}}  " + "  ".join(c[:3].upper() for c in _CAPS)
    print(head)
    print("-" * len(head))
    for r in rows:
        cells = "  ".join((" x " if r[c] else " . ") for c in _CAPS)
        print(f"{r['id']:<{wid}}  {r['label']:<{lwid}}  {cells}")
    print(f"\n{len(rows)} format{'' if len(rows) == 1 else 's'}  (x = supported, . = not)")


def run(args):
    rows = _matrix()
    if args.format:
        rows = [r for r in rows if r["id"] == args.format.lower()]
        if not rows:
            print(f"acidcat formats: no format {args.format!r} "
                  f"(try `acidcat formats` for the list)", file=sys.stderr)
            return 1

    if args.output_format == "json":
        json.dump(rows, sys.stdout, indent=2)
        print()
    elif args.output_format == "tsv":
        print("id\tlabel\t" + "\t".join(_CAPS))
        for r in rows:
            print(r["id"] + "\t" + r["label"] + "\t"
                  + "\t".join("1" if r[c] else "0" for c in _CAPS))
    else:
        _print_table(rows)
    return 0
