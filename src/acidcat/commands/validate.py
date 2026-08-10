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
    p.add_argument("--deep", action="store_true",
                   help="Also verify the checksums a format carries about "
                        "itself: FLAC frame CRCs, MP3 frame validity. These "
                        "PROVE damage rather than infer it, but cost a full "
                        "read (~10 MB/s), so they are off by default.")
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


def _deep_check(path, data):
    """Verify the integrity data the format carries about itself.

    Structural analysis asks whether the container's own arithmetic adds up.
    This asks a different and stronger question: does the payload still match
    the checksum written over it? A failure here is proof, not inference.

    Returns a one-line finding, or None when the format carries nothing
    checkable or everything checks out. Only FLAC and MP3 for now; both are
    verifiable without decoding, which matters because acidcat bundles no
    decoders.
    """
    from acidcat.core.forensics import checksums

    head = bytes(data[:4])
    if head == b"fLaC":
        pos = 4
        while pos + 4 <= len(data):
            hdr = data[pos]
            ln = int.from_bytes(bytes(data[pos + 1:pos + 4]), "big")
            pos += 4 + ln
            if hdr & 0x80:
                break
        else:
            return None
        r = checksums.flac_frames(data, pos, len(data))
        if r["failed"]:
            where = ", ".join(f"0x{o:08x}" for o in r["offsets"][:3])
            return (f"{r['failed']} of {r['checked']} FLAC frame(s) fail their "
                    f"CRC-16 (at {where}) -- the audio no longer matches the "
                    f"checksum the encoder wrote over it")
        return None

    start = 0
    if head[:3] == b"ID3":
        b = bytes(data[:10])
        start = 10 + ((b[6] & 0x7F) << 21 | (b[7] & 0x7F) << 14
                      | (b[8] & 0x7F) << 7 | (b[9] & 0x7F))
    elif not (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return None
    r = checksums.mp3_frames(data, start)
    bad = r["resyncs"] + r["bad_bigvalues"] + r["bad_backref"]
    if bad and r["frames"]:
        where = ", ".join(f"0x{o:08x}" for o in r["offsets"][:3])
        return (f"{bad} damaged MP3 frame(s) of {r['frames']} (at {where}): "
                f"{r['resyncs']} lost sync, {r['bad_bigvalues']} impossible "
                f"big_values, {r['bad_backref']} dangling bit-reservoir "
                f"back-reference(s)")
    return None


def _check(path, quiet, rows=None, deep=False):
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
    deep_note = None
    try:
        with memoryview(data) as view:
            report = constraints.analyze(view)
        if deep:
            deep_note = _deep_check(path, data)
    finally:
        close()
    if report is None:
        # A format acidcat does not structurally model can still carry a
        # checksum over its own bytes -- MP3 is exactly that case -- so --deep
        # has a verdict here even though the structural pass does not.
        if deep_note:
            if rows is not None:
                rows.append({"path": path, "format": None, "status": "fail",
                             "issues": 1, "repairable": False,
                             "detail": deep_note})
            else:
                print(f"FAIL  {os.path.basename(path)}  [deep]  1 issue(s)")
                print(f"        {deep_note}")
            return True, False, False, False
        if rows is not None:
            # a skip is a real answer and belongs in the record set, so a
            # consumer can tell "checked, clean" from "never modelled"
            rows.append({"path": path, "format": None, "status": "skipped",
                         "issues": 0, "repairable": False,
                         "detail": "not a structurally-modeled container"})
        return False, True, False, False        # not a structurally-modeled container
    base = os.path.basename(path)
    if not report.violations and not deep_note:
        if rows is not None:
            rows.append({"path": path, "format": report.label, "status": "ok",
                         "issues": 0, "repairable": False, "detail": ""})
        elif not quiet:
            print(f"OK    {base}  [{report.label}]")
        return True, True, False, False
    if not report.violations:
        # structurally sound, but a checksum over its own payload disagrees --
        # which is a stronger statement than any structural check can make
        if rows is not None:
            rows.append({"path": path, "format": report.label, "status": "fail",
                         "issues": 1, "repairable": False, "detail": deep_note})
        else:
            print(f"FAIL  {base}  [{report.label}]  1 issue(s)")
            print(f"        {deep_note}")
        return True, False, False, False
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
    from acidcat.util.stdin import resolved_input
    from contextlib import ExitStack
    # `-` is stdin, resolved up front; real paths pass through unchanged.
    with ExitStack() as stack:
        args.inputs = [
            stack.enter_context(resolved_input(t)) if t == "-" else t
            for t in args.inputs
        ]
        if any(t is None for t in args.inputs):
            print("acidcat validate: no data on stdin", file=sys.stderr)
            return 1
        return _run(args)


def _run(args):
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
            did, ok, error, repairable = _check(
                path, args.quiet, rows, deep=getattr(args, 'deep', False))
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
