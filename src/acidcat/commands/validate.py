"""acidcat validate -- report structural constraint violations, read-only.

The read-only face of the constraint model (core/constraints): it runs the same
analysis ``repair`` uses, but writes nothing and returns an exit code, so it fits
a CI check or a sweep over a whole library to find the broken files before they
bite. A file whose container acidcat does not model structurally is skipped, not
failed.

    acidcat validate FILE...            # check specific files
    acidcat validate DIR                # walk a directory tree
    acidcat validate DIR -q             # only print files with issues

Exit status: 0 when every checked file is consistent, 1 when any file has a
violation, 2 on a usage error.
"""

import os
import sys

from acidcat.commands._output import add_output_format_arg
from acidcat.core.infra.render import output as _render
from acidcat.core.infra.mapped import map_file
from acidcat.core.write import constraints

_EXTS = (".wav", ".rf64", ".bwf", ".aif", ".aiff", ".aifc", ".sf2", ".sf3",
         ".m4a", ".mp4", ".mov", ".m4b", ".flac")


def register(subparsers):
    p = subparsers.add_parser(
        "validate",
        help="Check container structure for stale size/offset/pad fields (read-only).")
    p.add_argument("inputs", nargs="+", help="File(s) or directory(ies) to check.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Only print files that have violations.")
    # validate is the CI-gate verb: you could branch on its exit code but not
    # read WHICH file failed or WHY without scraping the human table. json
    # carries the violations nested; csv/tsv flatten to one row per file.
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.set_defaults(func=run)


def _iter_paths(inputs):
    for inp in inputs:
        if os.path.isdir(inp):
            for root, _dirs, files in os.walk(inp):
                for name in sorted(files):
                    if name.lower().endswith(_EXTS):
                        yield os.path.join(root, name)
        else:
            yield inp


def _check(path, quiet, rows=None):
    """Return (checked, ok, error, repairable): checked is False for a skipped or
    unreadable file; error is True only when the file could not be read (I/O
    error), as opposed to being a format acidcat does not structurally model (a
    clean skip). When `rows` is given, append a record instead of printing --
    the same verdict, in the machine's shape.
    """
    # Mapped, not slurped -- the same treatment `audit` gives the same bytes.
    # constraints.analyze only walks the chunk-size cascade and never needs a
    # payload, but f.read() pulled the whole file in: on a 41 MB WAV that was
    # 82.9 MB of Python heap against 0.01 MB mapped, and validate's footprint
    # scaled with input where audit's stayed flat. A directory sweep over a
    # library of multi-GB RF64 files was slurping each one entire.
    # The memoryview matters as much as the map: the IFF engine keeps a slice
    # of every chunk it parses, and a view slice is a zero-copy window where a
    # bytes slice would materialize the payload.
    try:
        data, close = map_file(path)
    except OSError as e:
        print(f"acidcat validate: {path}: {e}", file=sys.stderr)
        if rows is not None:
            rows.append({"path": path, "format": None, "status": "unreadable",
                         "issues": 0, "repairable": False, "detail": str(e)})
        return False, True, True, False
    try:
        with memoryview(data) as view:
            report = constraints.analyze(view)
    finally:
        close()
    if report is None:
        if rows is not None:
            # a skip is a real answer and belongs in the record set, so a
            # consumer can tell "checked, clean" from "never modelled"
            rows.append({"path": path, "format": None, "status": "skipped",
                         "issues": 0, "repairable": False,
                         "detail": "not a structurally-modeled container"})
        return False, True, False, False        # not a structurally-modeled container
    base = os.path.basename(path)
    if not report.violations:
        if rows is not None:
            rows.append({"path": path, "format": report.label, "status": "ok",
                         "issues": 0, "repairable": False, "detail": ""})
        elif not quiet:
            print(f"OK    {base}  [{report.label}]")
        return True, True, False, False
    if rows is not None:
        rows.append({"path": path, "format": report.label, "status": "fail",
                     "issues": len(report.violations),
                     "repairable": any(v.repairable for v in report.violations),
                     "detail": "; ".join(v.describe() for v in report.violations),
                     "violations": [{"describe": v.describe(), "kind": v.kind,
                                     "field": v.field, "stored": v.stored,
                                     "computed": v.computed,
                                     "repairable": v.repairable}
                                    for v in report.violations]})
        return True, False, False, any(v.repairable for v in report.violations)
    print(f"FAIL  {base}  [{report.label}]  {len(report.violations)} issue(s)")
    for v in report.violations:
        mark = "" if v.repairable else "  (no witness)"
        print(f"        {v.describe()}{mark}")
    return True, False, False, any(v.repairable for v in report.violations)


def run(args):
    # grep/diff exit-code family: 0 = every checked file is consistent,
    # 1 = some file has a violation (ran fine), 2 = a named input could not be
    # accessed (a real error). a file inside a walked directory that is missing
    # or unreadable is a skip, not a hard error.
    checked = failed = errors = unreadable = 0
    any_repairable = False
    fmt = getattr(args, "output_format", "table")
    rows = None if fmt == "table" else []
    for inp in args.inputs:
        if not os.path.exists(inp):
            print(f"acidcat validate: {inp}: No such file or directory",
                  file=sys.stderr)
            errors += 1
            continue
        named = not os.path.isdir(inp)
        for path in _iter_paths([inp]):
            did, ok, error, repairable = _check(path, args.quiet, rows)
            any_repairable = any_repairable or repairable
            if error:
                # Inside a directory walk an unreadable file used to be counted
                # nowhere -- not checked, not failed, not an error -- so a run
                # over a library with locked files printed "all N consistent"
                # and exited 0. It is not a failure, but it is not a pass.
                if named:
                    errors += 1
                else:
                    unreadable += 1
            if did:
                checked += 1
                if not ok:
                    failed += 1
    if rows is not None:
        # csv/tsv are one flat row per file; the nested per-violation detail
        # only survives in json, so drop the key rather than stringify a list
        # into a cell nobody can parse.
        if fmt in ("csv", "tsv"):
            rows = [{k: v for k, v in r.items() if k != "violations"} for r in rows]
        _render(rows, fmt=fmt)
    if errors:
        return 2
    skipped = f", {unreadable} unreadable (not checked)" if unreadable else ""
    if checked == 0:
        # 2, not 0. `validate` is the natural gate in a script, and returning
        # success for files it never modelled gave a clean bill of health to
        # anything it did not understand -- `validate garbage.bin` and
        # `validate track.mod` both said "fine" while `audit` on the same byte
        # had findings. Nothing checked is "could not do the job", the same
        # class as an unreadable input, not a passing result.
        print("acidcat validate: no structurally-modeled files to check"
              + skipped, file=sys.stderr)
        return 2
    if failed:
        # only point at repair when something is actually repairable. An
        # orphaned audio payload has no safe rewrite and repair refuses it, so
        # the advice would send the user round a loop.
        hint = " (fix with: acidcat repair)" if any_repairable else ""
        # stdout belongs to the records in a machine format -- a trailing human
        # sentence made the JSON unparseable ("Extra data")
        print(f"\n{failed} of {checked} file(s) have structural issues"
              f"{hint}{skipped}", file=sys.stderr if rows is not None else sys.stdout)
        return 1
    if not args.quiet:
        print(f"\nall {checked} file(s) consistent{skipped}",
              file=sys.stderr if rows is not None else sys.stdout)
    return 1 if unreadable else 0
