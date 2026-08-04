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


def _check(path, quiet):
    """Return (checked, ok, error): checked is False for a skipped or unreadable
    file; error is True only when the file could not be read (I/O error), as
    opposed to being a format acidcat does not structurally model (a clean skip)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"acidcat validate: {path}: {e}", file=sys.stderr)
        return False, True, True, False
    report = constraints.analyze(data)
    if report is None:
        return False, True, False, False        # not a structurally-modeled container
    base = os.path.basename(path)
    if not report.violations:
        if not quiet:
            print(f"OK    {base}  [{report.label}]")
        return True, True, False, False
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
    for inp in args.inputs:
        if not os.path.exists(inp):
            print(f"acidcat validate: {inp}: No such file or directory",
                  file=sys.stderr)
            errors += 1
            continue
        named = not os.path.isdir(inp)
        for path in _iter_paths([inp]):
            did, ok, error, repairable = _check(path, args.quiet)
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
        print(f"\n{failed} of {checked} file(s) have structural issues"
              f"{hint}{skipped}")
        return 1
    if not args.quiet:
        print(f"\nall {checked} file(s) consistent{skipped}")
    return 1 if unreadable else 0
