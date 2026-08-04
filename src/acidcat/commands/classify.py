"""acidcat classify -- what kind of thing is this, and what should look at it next.

The triage question, before any expensive analysis: is this a single file we
understand, a bigger thing with files inside, damaged remains of either, or not
audio at all. Each verdict names the verb that follows, so an unknown file is
the start of a workflow rather than a dead end.

    acidcat classify mystery.bin           # one verdict, with its evidence
    acidcat classify samples/ --json       # triage a whole tree
    acidcat classify huge.img --shallow    # magic + structure only, no sweep

Cheap by construction: magic detection is ~0.08 ms, the embedded-container
sweep ~76 ms on 32 MB. The statistical audio scan (~13 s on the same file) is
never run here -- when it is the right next step, that is reported, not done.
"""

import json
import os
import sys

from acidcat.commands._output import add_output_format_arg
from acidcat.core.forensics.classify import classify as classify_file
from acidcat.core.infra.render import output
from acidcat.util.color import add_color_arg, color_enabled
from acidcat.util.stdin import display_name

_SHAPE_COLOR = {
    "single": "32",       # green: understood
    "container": "36",    # cyan: holds things
    "chunked": "36",
    "unwalked": "35",     # magenta: we know what it is, we just do not parse it
    "damaged": "33",      # yellow: recoverable with work
    "opaque": "90",       # dim: nothing structural
    "foreign": "90",
    "empty": "90",
}


def register(subparsers):
    p = subparsers.add_parser(
        "classify",
        help="Triage a file: single format, container, damaged, or not audio -- "
             "and what to run next.")
    p.add_argument("targets", nargs="+", metavar="target",
                   help="Files or directories to triage.")
    p.add_argument("--shallow", action="store_true",
                   help="Magic and chunk structure only -- skip the embedded "
                        "container sweep and resync. For large trees where the "
                        "per-file sweep would dominate.")
    add_output_format_arg(p, only=("table", "json", "csv"))
    add_color_arg(p)
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Only report files that are not a plainly-understood "
                        "single format.")
    p.set_defaults(func=run)


def _iter_targets(targets):
    for t in targets:
        if os.path.isdir(t):
            for root, _dirs, files in os.walk(t):
                for fn in sorted(files):
                    yield os.path.join(root, fn)
        else:
            yield t


def _c(code, text, on):
    return f"\033[{code}m{text}\033[0m" if on else text


# verdicts that mean "there is nothing here acidcat can work with". Every other
# shape names something it understood well enough to hand to another verb.
_NOTHING_FOUND = {"opaque", "foreign", "empty"}


def run(args):
    on = color_enabled(args)
    fmt = getattr(args, "output_format", "table")
    rows, exit_code = [], 0
    # counted separately from `rows` because --quiet drops the `single` rows,
    # and "nothing interesting to show" is a success, not a negative result
    identified = 0

    for path in _iter_targets(args.targets):
        try:
            v = classify_file(path, deep=not args.shallow)
        except OSError as e:
            print(f"acidcat classify: {path}: {e}", file=sys.stderr)
            exit_code = 2                      # could not read it, not a verdict
            continue
        if v["shape"] not in _NOTHING_FOUND:
            identified += 1
        if args.quiet and v["shape"] == "single":
            continue
        rows.append({"file": display_name(path), "shape": v["shape"],
                     "format": v["format"] or "", "next": v["next"] or "",
                     "detail": v["detail"], "path": path,
                     "evidence": v["evidence"]})

    # 1 when nothing among the targets was identifiable, so `classify f &&
    # inspect f` stops instead of running inspect on a file classify just
    # called opaque. A read failure (2) outranks it.
    if not exit_code and not identified:
        exit_code = 1

    if fmt == "json":
        json.dump(rows, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return exit_code
    if fmt == "csv":
        output([{k: r[k] for k in ("file", "shape", "format", "next", "detail")}
                for r in rows], fmt="csv")
        return exit_code

    if not rows:
        print("(nothing to report)", file=sys.stderr)
        return exit_code
    wid = min(38, max(len(r["file"]) for r in rows))
    for r in rows:
        shape = _c(_SHAPE_COLOR.get(r["shape"], "0"), f"{r['shape']:9}", on)
        nxt = _c("1", r["next"], on) if r["next"] else _c("90", "-", on)
        print(f"{r['file'][:wid]:<{wid}}  {shape}  {r['detail']}")
        if r["next"]:
            print(f"{'':<{wid}}  {'':9}  next: {nxt} "
                  f"{_shell_quote(r['file'])}")
    return exit_code


def _shell_quote(name):
    return f'"{name}"' if any(c in name for c in ' \t&()+;') else name
