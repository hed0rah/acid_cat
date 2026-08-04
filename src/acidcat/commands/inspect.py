"""
acidcat inspect -- readelf-style structural dump for audio files.

Walks the container chunk by chunk and prints a structural table, a
decoded field breakdown per known chunk (with byte offsets), and any
spec violations it noticed along the way. `--hex` adds the raw bytes
next to each decoded field. `--frames` adds a per-element deep dump
(every MPEG frame for MP3, every event for MIDI). `--color` syntax-
highlights the table (auto/always/never, respects NO_COLOR). `--json`
emits the same structure for machines.

The format walkers live in acidcat/core/walk and are dispatched through
its registry, so what `inspect` supports is exactly what the registry
holds -- `acidcat formats` prints it. Do not enumerate the list here: a
hardcoded subset in a docstring reads as the whole set and goes stale
every time a walker lands.

This module is the CLI shell: argument parsing, chunk selection, and
rendering.
"""

import contextlib
import json
import os
import sys
from acidcat.util.color import add_color_arg, color_enabled

from acidcat.core.forensics import anomalies as anomaliesmod
from acidcat.commands._output import add_output_format_arg
from acidcat.core.forensics import lsb as lsbmod
from acidcat.core.walk import Unsupported, walk_file
from acidcat.util.region import add_region_args, scoped_file

# --full emits raw region bytes for chunks that have decoded fields; cap the
# hex so a huge header (embedded art) cannot bloat the dump without bound.
_FULL_RAW_CAP = 8192


def register(subparsers):
    p = subparsers.add_parser(
        "inspect",
        help="readelf-style structural dump of an audio or synth/DAW preset file.",
    )
    p.add_argument("targets", nargs="+", metavar="target",
                   help="One or more audio, sampler or synth/DAW preset files. "
                        "Run `acidcat formats` for the full list of what has a "
                        "walker. With more than one target, each is printed "
                        "under a 'File:' banner; JSON output becomes NDJSON "
                        "(one record per line).")
    p.add_argument("--hex", action="store_true", dest="show_hex",
                   help="Show raw bytes next to each decoded field.")
    add_output_format_arg(p, only=("table", "json"))
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Chunk table only, no per-chunk field detail.")
    p.add_argument("--pretty", action="store_true",
                   help="Human-friendly view of the decoded tags and metadata "
                        "(no byte offsets), ideal for presets and tagged files.")
    p.add_argument("-F", "--frames", action="store_true",
                   help="Per-element deep dump: every MPEG frame (MP3) or "
                        "MIDI event. No effect on formats without per-element "
                        "structure (WAV, AIFF, FLAC).")
    p.add_argument("--only", metavar="IDS",
                   help="Show only these chunk ids (comma-separated, e.g. "
                        "'fmt,bext'). Case-insensitive, matched against the "
                        "displayed id. Compose with --hex to hexdump one chunk.")
    p.add_argument("--exclude", metavar="IDS",
                   help="Hide these chunk ids (comma-separated). Applied after "
                        "--only.")
    p.add_argument("--full", action="store_true",
                   help="Emit a self-contained structural dump (implies --json): "
                        "each chunk with its raw region bytes and every field's "
                        "absolute byte offset, so `acidcat explore` can render a "
                        "standalone HTML explorer for the file.")
    p.add_argument("--anomalies", action="store_true",
                   help="Forensic scan: flag trailing data past the container, "
                        "appended-format magic (polyglots), structural size "
                        "mismatches, and control bytes smuggled into text fields.")
    add_color_arg(p)
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Synonym for --frames: request the walker's deep pass. "
                        "What that adds is per-format -- every MPEG frame, "
                        "every MIDI event, the Bitwig device tree, the Vital "
                        "modulation matrix, the NI compressed subtree.")
    # experimental: parse untrusted input in a resource-limited worker so a
    # memory/CPU-bomb file takes down only the worker. Linux only; --sandbox
    # errors (never silently runs unsandboxed) where it cannot run.
    p.add_argument("--sandbox", action="store_true",
                   help="Parse untrusted input in an isolated worker (experimental, "
                        "Linux only).")
    p.add_argument("--sandbox-profile", choices=["auto", "limits", "bwrap"],
                   default="auto",
                   help="auto (strongest available, default), limits (setrlimit "
                        "fork), or bwrap (namespace: no network, no filesystem "
                        "beyond the runtime + input).")
    p.add_argument("--sandbox-mem", type=int, default=None, metavar="MB",
                   help="--sandbox address-space cap in MB (default 2048).")
    p.add_argument("--sandbox-timeout", type=int, default=None, metavar="S",
                   help="--sandbox CPU/wall-clock cap in seconds (default 60).")
    # reverse-engineering escapes: name the format yourself, scope to a region
    # inside a bigger image, or just tell it to try.
    p.add_argument("--format", metavar="FMT", dest="fmt_override",
                   help="Parse as FMT regardless of the magic bytes (an odd or "
                        "old variant of a format we do model often walks fine "
                        "once dispatch stops depending on the header). "
                        "`acidcat formats` lists the ids.")
    p.add_argument("--force", action="store_true",
                   help="On a file no walker claims, try every walker and report "
                        "what each made of it -- chunk/field counts, whether the "
                        "chunk ids are really at those offsets, and the walker's "
                        "own complaint. Leads for --format, not identifications.")
    p.add_argument("--resync", action="store_true",
                   help="Recover chunk structure from a damaged container by "
                        "scanning for plausible [id][size] records and keeping "
                        "the ones that chain end-to-start. Finds what a corrupt "
                        "size field or a smashed magic costs the normal walk.")
    add_region_args(p)
    p.set_defaults(func=run)


# ── rendering ──────────────────────────────────────────────────────


def _hex_bytes(filepath, offset, length, cap=8):
    with open(filepath, "rb") as f:
        f.seek(offset)
        raw = f.read(min(length, cap))
    s = raw.hex(" ")
    return s + " .." if length > cap else s


# ── color ──────────────────────────────────────────────────────────
# small, meaningful palette: structure (cyan), value (green), positional
# metadata (dim), warning (red). codes are zero-width, so callers pad to
# the column width first and paint the padded string.

_ANSI = {
    # bright-black (a real palette slot the terminal theme defines) rather
    # than faint (\033[2m): terminals render faint by blending the fg toward
    # the background, which turns muddy on any non-black background. 90 stays
    # legible against whatever background the user's theme actually uses.
    "dim": "\033[90m",
    "id": "\033[1;36m",     # bold cyan: chunk ids, format label, anchors
    "val": "\033[32m",      # green: decoded field values
    "warn": "\033[1;31m",   # bold red: warnings
}
_RESET = "\033[0m"




class _Paint:
    def __init__(self, on):
        self.on = on

    def __call__(self, role, text):
        text = str(text)
        return f"{_ANSI[role]}{text}{_RESET}" if self.on else text


def _render_rows(rows, paint):
    """Print a per-element listing as a compact dynamic-column table."""
    if not rows:
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = "    " + "  ".join(f"{c:<{widths[c]}}" for c in cols)
    print(paint("dim", header))
    for r in rows:
        print("    " + "  ".join(f"{str(r.get(c, '')):<{widths[c]}}" for c in cols))


def _human_size(n):
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return f"{int(x)} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024


def _render_pretty(filepath, fmt_label, chunks, file_warns, args):
    """A clean, human-friendly view of the decoded tags/metadata: section per
    chunk, aligned key/value, no byte offsets. Made for presets and tagged
    files (Bitwig, Vital, Serum, MP4 tags, WAV/FLAC/MP3 metadata)."""
    p = _Paint(color_enabled(args))
    size = os.path.getsize(filepath)
    print(p("id", os.path.basename(filepath)))
    print(p("dim", f"{fmt_label}, {_human_size(size)}"))
    for c in chunks:
        fields = [f for f in c["fields"]
                  if f["value"] not in (None, "") and str(f["value"]).strip()]
        if not fields:
            continue
        print()
        head = c["id"].strip()
        meta = f"  {p('dim', c['summary'])}" if c.get("summary") else ""
        print(p("id", head) + meta)
        w = max(len(f["name"]) for f in fields)
        for f in fields:
            key = p("dim", f"{f['name']:<{w}}")
            note = f"  {p('dim', '(' + str(f['note']) + ')')}" if f["note"] else ""
            print(f"  {key}  {p('val', f['value'])}{note}")
    all_warns = list(file_warns) + [w for c in chunks for w in c["warnings"]]
    if all_warns:
        print()
        print(p("warn", "warnings:"))
        for w in all_warns:
            print(p("warn", f"  ! {w}"))
    return 0


def _render_anomalies(findings, args):
    """Print the forensic findings from `--anomalies` under the main dump."""
    p = _Paint(color_enabled(args))
    role = {"alert": "warn", "warn": "warn", "notice": "dim"}
    print()
    if not findings:
        print(p("dim", "  anomalies: none"))
        return
    print(p("id", f"  anomalies ({len(findings)}):"))
    for f in findings:
        sev = f["severity"]
        tag = p(role.get(sev, "dim"), f"[{sev:6}]")
        off = p("dim", f"0x{f['offset']:08x}")
        print(f"    {tag} {off}  {f['rule']:16} {f['message']}")


def _render_table(filepath, fmt_label, chunks, file_warns, args, total=None):
    file_size = os.path.getsize(filepath)
    p = _Paint(color_enabled(args))
    if total is not None and total != len(chunks):
        count = f"showing {len(chunks)} of {total} chunks"
    else:
        count = f"{len(chunks)} chunks"
    print(f"{os.path.basename(filepath)}: {p('id', fmt_label)}, {file_size:,} bytes, "
          f"{count}")
    print()
    print(p("dim", f"  {'idx':<5} {'id':<5} {'offset':<11} {'size':<11} summary"))
    for i, c in enumerate(chunks):
        idx = p("dim", f"[{c.get('_idx', i):>2}]")
        cid = p("id", f"{c['id']:<5}")
        off = p("dim", f"0x{c['offset']:08x}")
        print(f"  {idx}  {cid} {off}  {c['size']:<11,} {c['summary']}")

    if not args.quiet:
        for c in chunks:
            if not c["fields"] and not c.get("rows"):
                continue
            print()
            hdr_id = p("id", c["id"].strip())
            hdr_meta = p("dim", f"@ 0x{c['offset']:08x} ({c['size']} bytes)")
            print(f"{hdr_id} {hdr_meta}")
            for fl in c["fields"]:
                note = p("dim", f"  {fl['note']}") if fl["note"] else ""
                # derived stats (midi track facts) carry no byte offset
                off_col = f"+0x{fl['off']:04x}" if fl["off"] is not None else "      "
                off_col = p("dim", off_col)
                val = p("val", f"{fl['value']!s:<14}")
                if args.show_hex and fl["off"] is not None:
                    # field offsets are measured from the chunk's payload base.
                    # RIFF/AIFF/RF64/MThd all have an 8-byte id+size header, so
                    # that is the default; formats with a different header (FLAC
                    # blocks: 4 bytes) or whose fields are already absolute (MP3
                    # ID3 tags, MPEG frames, the FLAC/Serum magic) set their own.
                    base = c.get("payload_base")
                    if base is None:
                        base = c["offset"] + 8
                    hx = _hex_bytes(filepath, base + fl["off"], fl["len"])
                    print(f"  {off_col}  {p('dim', f'{hx:<26}')} "
                          f"{fl['name']:<22} {val}{note}")
                else:
                    print(f"  {off_col}  {fl['name']:<22} {val}{note}")
            if c.get("rows"):
                _render_rows(c["rows"], p)

    if getattr(args, "frames", False) and not any(c.get("rows") for c in chunks):
        print()
        print(p("dim", f"  (--frames: {fmt_label} has no per-element structure to dump)"))

    all_warns = list(file_warns)
    all_warns += [f"{c['id'].strip()}: {w}" for c in chunks for w in c["warnings"]]
    if all_warns:
        print()
        print(p("warn", "warnings:"))
        for w in all_warns:
            print(p("warn", f"  ! {w}"))
    return 0


def _parse_id_list(val):
    """A comma-separated chunk-id list into a normalized set (or None)."""
    if not val:
        return None
    return {x.strip().casefold() for x in val.split(",") if x.strip()}


def _select_chunks(chunks, only, exclude):
    """Filter chunks by --only/--exclude, tagging each survivor with its
    original index so the table keeps truthful [n] and file positions."""
    out = []
    for i, c in enumerate(chunks):
        cid = c["id"].strip().casefold()
        if only is not None and cid not in only:
            continue
        if exclude is not None and cid in exclude:
            continue
        c = dict(c)
        c["_idx"] = i
        out.append(c)
    return out


# keys that exist on a field only to drive the interactive editor (encoding hint
# + numeric value to re-encode); they stay out of the public inspect JSON.
_EDITOR_FIELD_KEYS = ("enc", "raw")


def _public_field(f):
    return {k: v for k, v in f.items() if k not in _EDITOR_FIELD_KEYS}


def _full_chunk(chunk, filepath):
    """Enrich a chunk for --full into a self-contained record: its absolute
    payload base, the raw region bytes as hex (capped), and every field's
    absolute byte offset. `acidcat explore` needs nothing but this JSON."""
    c = {k: v for k, v in chunk.items() if k != "_idx"}
    pb = chunk.get("payload_base", chunk["offset"] + 8)
    c["payload_base"] = pb
    fields = []
    for f in chunk["fields"]:
        f2 = _public_field(f)
        # absolute file offset, so a field maps to raw[abs - offset]
        f2["abs"] = pb + f["off"] if f["off"] is not None else None
        fields.append(f2)
    c["fields"] = fields
    # only carry raw bytes for chunks that actually have positioned fields;
    # audio-data regions are huge and have nothing to highlight.
    if any(f["off"] is not None for f in chunk["fields"]):
        n = min(chunk["size"], _FULL_RAW_CAP)
        with open(filepath, "rb") as fh:
            fh.seek(chunk["offset"])
            raw = fh.read(n)
        c["raw"] = raw.hex()
        c["raw_base"] = chunk["offset"]
        if chunk["size"] > _FULL_RAW_CAP:
            c["raw_truncated"] = chunk["size"] - _FULL_RAW_CAP
    return c


def _run_resync(filepath, paint, source_path=None):
    """--resync: report the chunk grid still recoverable from a damaged container."""
    from acidcat.core.forensics import resync as resyncmod

    with open(filepath, "rb") as f:
        data = f.read()
    res = resyncmod.recover(data, known_only=True)
    chain, recs = res["chain"], res["records"]
    name = os.path.basename(source_path or filepath)
    if not chain:
        print(f"{name}: no recoverable chunk grid "
              f"({len(recs)} isolated record(s) found)")
        print(paint("dim",
                    "  nothing chains end-to-start, so there is no surviving\n"
                    "  structure to rebuild. If the payload is still in there,\n"
                    "  `acidcat locate` finds it statistically instead."))
        return 1
    print(f"{name}: recovered {len(chain)} chunk(s) by resync "
          f"[{res['endian']}-endian, {res['coverage']:.0%} of the file]")
    print(paint("dim", f"  {'offset':>10}  {'id':6} {'size':>12}  conf  evidence"))
    for r in chain:
        ev = []
        if r["known"]:
            ev.append("known id")
        if r["corroborated"]:
            ev.append("chains onward")
        print(f"  0x{r['offset']:08x}  {r['id']:6} {r['size']:>12,}  "
              f"{r['confidence']:.2f}  {', '.join(ev) or '-'}")
    print(paint("dim",
                "\n  found by scanning for [id][size] records and keeping the ones\n"
                "  that link end-to-start. Corroborated hypotheses, not a validated\n"
                "  parse -- carve one out to work on it:\n"
                f"    acidcat carve {name} --offset 0x{chain[0]['offset']:x} "
                f"--length {chain[0]['size'] + 8}"))
    return 0


_MAGIC_COMPLAINT = ("magic", "not a zip", "does not parse", "unknown iq",
                    "no .sigmf-meta", "spec says")


def _forced_candidates(filepath, deep):
    """Try every walker on a file none of them claims, and report what each one
    made of it -- ranked, with its own complaints attached.

    Deliberately NOT a single answer. Walkers assume their magic rather than
    verifying it, so a forced parse readily invents structure: pointed at an
    arbitrary blob, the MIDI walker reports an MThd chunk larger than the file
    and the FLAC walker reports a 'fLaC' magic that is not there. Picking a
    "winner" out of that would manufacture a false identification, which is
    worse than refusing. So this surfaces the candidates as leads for --format
    and lets the person decide.

    Each row carries: the chunk/field counts, whether the claimed sizes fit
    inside the real file (a parse claiming more bytes than exist is
    self-refuting), and the first thing the walker itself complained about.
    """
    from acidcat.core.walk import _WALKERS

    size = os.path.getsize(filepath)
    rows = []
    for fmt in _WALKERS:
        try:
            label, chunks, warns = walk_file(filepath, deep, fmt_override=fmt)
        except Exception:
            continue
        if not chunks:
            continue
        fits = all(c.get("offset", 0) + c.get("size", 0) <= size for c in chunks)
        ids_ok = all(str(c.get("id", "")).isprintable() for c in chunks)
        # the check a walker cannot talk its way past: if it reports a 4-byte id
        # at an offset, are those bytes actually there? A walker that assumes
        # its magic (FLAC reporting 'fLaC' over 03 13 a0 e0) fails this while
        # warning about nothing, which is exactly the silent fabrication that
        # would otherwise rank first.
        anchored = 0
        for c in chunks:
            cid = str(c.get("id", ""))
            if len(cid) == 4 and cid.isprintable():
                with open(filepath, "rb") as fh:
                    fh.seek(c.get("offset", 0))
                    if fh.read(4) == cid.encode("latin-1", "replace"):
                        anchored += 1
        complaint = next((w for w in warns
                          if any(k in w.lower() for k in _MAGIC_COMPLAINT)), "")
        rows.append({
            "format": fmt, "label": label,
            "chunks": len(chunks),
            "fields": sum(len(c.get("fields") or []) for c in chunks),
            "fits": fits, "ids_ok": ids_ok, "anchored": anchored,
            "complaint": complaint or (warns[0] if warns else ""),
        })
    # rank: a self-consistent parse that the walker did not complain about is
    # the strongest lead; a parse claiming bytes the file does not have is last
    # anchored ids first: bytes on disk beat a walker's silence. a parse that
    # invents its magic ranks below one that admits a problem but reads real ids.
    rows.sort(key=lambda r: (r["anchored"], r["fits"], r["ids_ok"],
                             r["fields"], r["chunks"]), reverse=True)
    return rows


def _print_forced_candidates(filepath, rows, paint):
    base = os.path.basename(filepath)
    arg = f'"{base}"' if any(c in base for c in ' \t&()+;') else base
    print(f"no walker claims {base}. forced-parse candidates "
          f"(hypotheses, not identifications):\n")
    print(paint("dim", f"  {'format':12} {'chunks':>6} {'fields':>6} {'ids':>4}"
                       f" {'sane':>5}  walker's own complaint"))
    for r in rows[:10]:
        sane = "yes" if (r["fits"] and r["ids_ok"]) else "NO"
        ids = f"{r['anchored']}/{r['chunks']}"
        note = r["complaint"][:46]
        line = (f"  {r['format']:12} {r['chunks']:6} {r['fields']:6} {ids:>4}"
                f" {sane:>5}  {note}")
        print(line if r["anchored"] else paint("dim", line))
    print(f"\n  none of these verified a magic number -- a walker parses at fixed"
          f"\n  offsets whether or not the header is really its format. Follow one"
          f"\n  up with:  acidcat inspect {arg} --format <id>")


def run(args):
    # accept either the multi-file `targets` or the legacy single `target`
    targets = getattr(args, "targets", None)
    if not targets:
        one = getattr(args, "target", None)
        targets = [one] if one else []
    if not targets:
        print("acidcat inspect: no target file given", file=sys.stderr)
        return 2

    deep = getattr(args, "frames", False) or getattr(args, "verbose", False)
    full = getattr(args, "full", False)
    as_json = args.output_format == "json" or full  # --full is a JSON dump
    multi = len(targets) > 1
    only = _parse_id_list(getattr(args, "only", None))
    exclude = _parse_id_list(getattr(args, "exclude", None))
    exit_code = 0

    # resolve the sandbox profile once, up front: --sandbox fails loud (never
    # silently runs unsandboxed) if the requested isolation cannot run here.
    sandbox_profile = None
    if getattr(args, "sandbox", False):
        from acidcat.core.infra import sandbox as _sb
        try:
            sandbox_profile = _sb.resolve_profile(getattr(args, "sandbox_profile", "auto"))
        except _sb.SandboxUnavailable as e:
            print(f"acidcat inspect: --sandbox: {e}", file=sys.stderr)
            return 2               # asked for an isolation mode we cannot run
        if not getattr(args, "quiet", False):
            print(f"[sandbox: {sandbox_profile}]", file=sys.stderr)

    # --region/--offset scope every verb below to a range inside a bigger file,
    # so a blob `locate` found in a disk image is walked directly instead of
    # being carved out by hand first. The copies live until run() returns,
    # because rendering re-reads the file after the walk (--hex).
    regions = contextlib.ExitStack()
    try:
        for filepath in targets:
            if not os.path.isfile(filepath):
                print(f"acidcat inspect: {filepath}: No such file", file=sys.stderr)
                exit_code = 2          # could not read it, as everywhere else
                continue
            source_path = filepath          # for messages: never leak the temp copy
            try:
                filepath, region_scope = regions.enter_context(
                    scoped_file(args, filepath))
            except (ValueError, OSError) as e:
                # a malformed --offset/--at/--region is a USAGE error, and `od`
                # and `carve` already return 2 for the identical mistake. This
                # returned 1, so a script could not branch on it without
                # knowing which verb it had called.
                print(f"acidcat inspect: {source_path}: {e}", file=sys.stderr)
                exit_code = 2
                continue
            if getattr(args, "resync", False):
                # a damaged container is exactly the case where the walk fails,
                # so recovery runs instead of it rather than after it
                rc = _run_resync(filepath, _Paint(color_enabled(args)),
                                 source_path=source_path)
                exit_code = exit_code or rc
                continue
            try:
                if sandbox_profile:
                    from acidcat.core.infra import sandbox as _sb
                    try:
                        fmt_label, chunks, file_warns = _sb.run_walk(
                            filepath, deep, profile=sandbox_profile,
                            mem_mb=args.sandbox_mem or _sb.DEFAULT_MEM_MB,
                            timeout_s=args.sandbox_timeout or _sb.DEFAULT_TIMEOUT_S)
                    except _sb.SandboxError as e:
                        print(f"acidcat inspect: {filepath}: sandbox: {e}",
                              file=sys.stderr)
                        exit_code = 1
                        continue
                else:
                    fmt_label, chunks, file_warns = walk_file(
                        filepath, deep,
                        fmt_override=getattr(args, "fmt_override", None))
                    if region_scope:
                        fmt_label = f"{fmt_label}  [region {region_scope}]"
            except Unsupported as e:
                if getattr(args, "force", False):
                    rows = _forced_candidates(filepath, deep)
                    if rows:
                        _print_forced_candidates(
                            filepath, rows, _Paint(color_enabled(args)))
                        exit_code = 1     # still unidentified; these are leads
                        continue
                if True:
                    # "I have no walker for this" is the honest answer here --
                    # but a dead end is not. Point at the verbs that work on raw
                    # bytes, so an unknown container starts the RE workflow
                    # rather than ending it. Quote the name so the suggestion
                    # survives a copy-paste: banks often have spaces and '+'.
                    base = os.path.basename(source_path)
                    arg = f'"{base}"' if any(c in base for c in ' \t&()+;') else base
                    scoped = f" (region {region_scope})" if region_scope else ""
                    print(f"acidcat inspect: {source_path}{scoped}: {e}",
                          file=sys.stderr)
                    print(f"  no structural walker, but the bytes are still yours:\n"
                          f"    acidcat od {arg}                hex dump, no format needed\n"
                          f"    acidcat locate {arg}            find embedded audio regions\n"
                          f"    acidcat inspect {arg} --force   try every walker anyway\n"
                          f"    acidcat inspect {arg} --format wav   parse as a known type",
                          file=sys.stderr)
                    exit_code = 1
                    continue
            except Exception as e:  # a walker bug must not sink the whole run
                print(f"acidcat inspect: {filepath}: {e.__class__.__name__}: {e}",
                      file=sys.stderr)
                exit_code = 1
                continue

            total = len(chunks)
            shown = _select_chunks(chunks, only, exclude)
            findings = (anomaliesmod.scan(filepath, fmt_label, chunks, file_warns)
                        if getattr(args, "anomalies", False) else None)
            lsb_info = None
            if getattr(args, "anomalies", False) or full:
                try:
                    lsb_info = lsbmod.analyze(filepath, fmt_label, chunks)
                except Exception:
                    lsb_info = None
            if findings is not None and lsb_info and lsb_info["uniform_high"]:
                findings.append({
                    "severity": "notice", "offset": lsb_info["region"][0],
                    "rule": "lsb_entropy",
                    "message": f"uniformly high LSB entropy (min {lsb_info['min']}, "
                               f"mean {lsb_info['mean']}): consistent with LSB "
                               f"steganography, but also with a noisy/dithered/"
                               f"high-bit-depth recording"})
                findings.sort(key=lambda x: (
                    -{"alert": 3, "warn": 2, "notice": 1}.get(x["severity"], 0),
                    x["offset"]))

            if as_json:
                # NDJSON: one compact record per file per line, so the stream
                # pipes cleanly into jq -c and other line-oriented tools.
                if full:
                    out_chunks = [_full_chunk(c, filepath) for c in shown]
                else:
                    out_chunks = []
                    for c in shown:
                        oc = {k: v for k, v in c.items() if k != "_idx"}
                        oc["fields"] = [_public_field(f) for f in c.get("fields", [])]
                        out_chunks.append(oc)
                sys.stdout.write(json.dumps({
                    "file": filepath,
                    "format": fmt_label,
                    "size": os.path.getsize(filepath),
                    "full": full,
                    "chunks": out_chunks,
                    "warnings": file_warns,
                    **({"anomalies": findings} if findings is not None else {}),
                    **({"lsb": lsb_info} if lsb_info else {}),
                }) + "\n")
            else:
                pretty = getattr(args, "pretty", False)
                if multi and not pretty:
                    print(f"\nFile: {filepath}")  # readelf-style per-file banner
                elif multi:
                    print()  # separate files; --pretty prints its own name header
                if pretty:
                    _render_pretty(filepath, fmt_label, shown, file_warns, args)
                else:
                    _render_table(filepath, fmt_label, shown, file_warns, args, total)
                if findings is not None:
                    _render_anomalies(findings, args)
    except BrokenPipeError:
        # a downstream pager or `head` closed the pipe: exit quietly the way
        # cat and grep do, without a traceback.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return exit_code
    finally:
        regions.close()

    return exit_code
