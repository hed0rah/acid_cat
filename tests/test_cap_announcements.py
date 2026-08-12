"""Every bound in the tree is accounted for, and a bound that bites says so.

This exists because the same defect kept arriving from different directions: a
cap, filter or read window applied, and the bounded result then presented as the
whole answer. A scan reporting its 20-item cap as "20 concealed sectors" when
there were 65. A directory walk printing "all N consistent" for a tree it never
opened. A 64 MB read cap reporting a CRC failure on a pristine file, because it
stopped mid-frame and judged the fragment. Nine of those were fixed one at a
time; this file is the attempt to stop the tenth.

TWO HALVES, and the second matters more.

The SWEEP patches a cap small, feeds an input that crosses it, and asserts the
announcement appears. That proves the sites it covers.

The LEDGER proves the sites it does not. Every module-level constant whose name
claims it bounds something must be in exactly one bucket: swept, exempt with a
reason from a closed set, or pending with a target. A new `_FOO_CAP` cannot be
added without landing in one of them, and the pending list can only shrink.
Without that, a registry is just a list of the caps somebody remembered.

WHY NOT A CLI SUBPROCESS SWEEP. monkeypatch cannot reach across a process
boundary, so forcing a cap small in a subprocess would mean an env-var override
at every site -- a production change to make a test possible. The sweep runs
in-process against the core function and asserts on the returned warnings;
whether a warning survives to the screen is a property of the renderer, tested
separately and once, not re-tested at every site.
"""

import ast
import enum
import importlib
import pathlib
import re
import struct

import pytest

SRC = pathlib.Path(__file__).parent.parent / "src" / "acidcat"
ROOTS = ("core", "tui_app", "commands", "util")

# a name that claims to bound something. The regex IS the contract, so it is
# guarded below: if it stops matching, this whole file asserts nothing.
_NAME = re.compile(r"^_[A-Z0-9_]*(?:CAP|MAX|LIMIT|FINDINGS|CANDS)[A-Z0-9_]*$")

_OPS = {ast.Mult: lambda a, b: a * b, ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b, ast.LShift: lambda a, b: a << b,
        ast.FloorDiv: lambda a, b: a // b, ast.Pow: lambda a, b: a ** b}


def _const_int(node):
    """The int an expression evaluates to, or None.

    ast.literal_eval is not enough and the difference is not academic: it
    refuses `64 * 1024 * 1024` and `1 << 22`, which is how most byte caps in
    this tree are spelled. Using it here dropped 33 of 84 constants, silently,
    including every _READ_CAP -- the exact class this file is about.
    """
    if isinstance(node, ast.Constant):
        return (node.value if isinstance(node.value, int)
                and not isinstance(node.value, bool) else None)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _const_int(node.left), _const_int(node.right)
        if a is not None and b is not None:
            try:
                return _OPS[type(node.op)](a, b)
            except (ValueError, ZeroDivisionError, OverflowError):
                return None
    return None


def declared_bounds():
    """(dotted_module, name, lineno, value) for every module-level int bound.

    AST rather than grep: it will not match a name inside a docstring or a
    comment, and it yields a line number, which is what makes a failure here
    actionable instead of annoying.
    """
    out = []
    for root in ROOTS:
        for py in sorted((SRC / root).rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:                     # pragma: no cover
                continue
            for node in tree.body:                  # module level, deliberately
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name) or not _NAME.match(target.id):
                    continue
                value = _const_int(node.value)
                if value is None:
                    continue
                rel = str(py.relative_to(SRC)).replace("\\", "/")[:-3]
                out.append(("acidcat." + rel.replace("/", "."),
                            target.id, node.lineno, value))
    return out


class Reason(enum.Enum):
    """Why a bound is not in the announce-it class. A closed set on purpose:
    a new cap cannot be filed under "misc"."""

    DEPTH_GUARD = "crossing it is a verdict about the file, not a shortened answer"
    SEARCH_WINDOW = "a lookahead inside a search; invisible in the result"
    RUNAWAY_BACKSTOP = "set far above any real file; crossing it is the anomaly"
    FIELD_SANITY = "a ceiling on a value READ from the file, used to reject it"
    RESOURCE_LIMIT = "crossing must raise, not truncate; asserted separately"
    VIEWPORT = "a scroll window; a view that scrolls is not a truncated report"


# (module, const) -> (Reason, prose). The prose must say where the behaviour IS
# covered, if anywhere. An exemption pointing at another test is a redirect; one
# pointing at nothing is a hole.
EXEMPT = {
    ("acidcat.core.census", "_MAX_DEPTH"):
        (Reason.DEPTH_GUARD, "nesting past this is malformed; census reports it"),
    ("acidcat.core.containers.gcm", "_MAX_DEPTH"):
        (Reason.DEPTH_GUARD, "covered by tests/test_container_hostile.py -- a "
                             "backwards child range must terminate, not truncate"),
    ("acidcat.core.containers.wiidisc", "_MAX_DEPTH"):
        (Reason.DEPTH_GUARD, "the gcm twin; same test asserts they have not diverged"),
    ("acidcat.core.formats.mp4", "_MAX_DEPTH"):
        (Reason.DEPTH_GUARD, "box nesting past 16 is malformed, not abridged"),
    ("acidcat.core.write.structure", "_MAX_NESTING"):
        (Reason.DEPTH_GUARD, "a write refuses rather than emitting a partial tree"),

    ("acidcat.core.forensics.checksums", "_LOOKAHEAD_CANDS"):
        (Reason.SEARCH_WINDOW, "how many spurious CRC-8 hits to step over while "
                               "finding one frame end; changes which candidate "
                               "wins, not how much of the file is represented"),
    ("acidcat.core.formats.mp3", "_FREE_SCAN_CAP"):
        (Reason.SEARCH_WINDOW, "the window used to measure a free-format frame"),

    ("acidcat.core.forensics.triage", "_WALK_CAP"):
        (Reason.RUNAWAY_BACKSTOP, "1,000,000 chunks against a real maximum in the "
                                  "thousands. If this is ever lowered below ~10x "
                                  "the largest real observation it leaves this "
                                  "category and must be swept"),
    ("acidcat.core.forensics.framescan", "_MAX_STREAMS"):
        (Reason.RUNAWAY_BACKSTOP, "4,096 distinct headerless streams in one image "
                                  "is not a real file"),

    ("acidcat.core.walk.mpc", "_INT64_MAX"):
        (Reason.FIELD_SANITY, "not a bound on our output at all; the largest "
                              "value the field can hold"),
    ("acidcat.core.forensics.checksums", "_MAX_FRAME"):
        (Reason.FIELD_SANITY, "no real FLAC frame approaches this; a larger span "
                              "means the frame does not verify"),
    ("acidcat.core.formats.bitwig", "_MAX_LEN"):
        (Reason.FIELD_SANITY, "a declared length larger than this is rejected"),

    ("acidcat.core.infra.sandbox", "_MAX_RESULT"):
        (Reason.RESOURCE_LIMIT, "a sandbox that truncated its own result and "
                                "returned it as complete would be worse than any "
                                "bug this file guards; it raises"),
    ("acidcat.core.infra.sandbox", "_FSIZE_CAP"):
        (Reason.RESOURCE_LIMIT, "rlimit on the worker; exceeding it kills the "
                                "worker rather than shortening an answer"),

    ("acidcat.tui_app.render", "_HEX_CAP"):
        (Reason.VIEWPORT, "the hex pane prints '.. N more bytes' and scrolls"),
    ("acidcat.tui_app.render", "_ROW_CAP"):
        (Reason.VIEWPORT, "the tree prints '... N more rows (+ to show more)'"),
    ("acidcat.tui_app.render", "_CHUNK_CAP"):
        (Reason.VIEWPORT, "prints '... N more chunks'; covered by "
                          "tests/test_tui_fundamentals.py"),
    ("acidcat.tui_app.render", "_HEXEDIT_CAP"):
        (Reason.VIEWPORT, "refuses to edit a region larger than this, and says so"),
    ("acidcat.tui_app.render", "_DIFF_CAP"):
        (Reason.VIEWPORT, "the count is the true total and the list is a prefix; "
                          "covered by tests/test_tui_fundamentals.py"),
    ("acidcat.tui_app.render", "_SEARCH_CAP"):
        (Reason.VIEWPORT, "the count is the true total and cycling is a prefix; "
                          "covered by tests/test_tui_fundamentals.py"),
    ("acidcat.tui_app.render", "_UNDO_CAP"):
        (Reason.VIEWPORT, "undo depth, not a report about the file"),
    ("acidcat.tui_app.render", "_UNDO_BYTES_CAP"):
        (Reason.VIEWPORT, "undo memory ceiling, not a report about the file"),
}


# Bounds that ARE in the class and are not yet swept. This list may shrink and
# must never grow: a new cap goes to SWEPT or EXEMPT, not here.
PENDING_1_0_1 = {
    ("acidcat.core.analysis.pcm", "_MAX_FRAMES"),
    ("acidcat.core.catalogue.search", "_CANDIDATE_CAP"),
    ("acidcat.core.census", "_MAX_CHUNKS"),
    ("acidcat.core.forensics.checksums", "_MAX_FINDINGS"),
    ("acidcat.core.forensics.checksums", "_READ_CAP"),
    ("acidcat.core.forensics.concealment", "_MAX_FINDINGS"),
    ("acidcat.core.forensics.framescan", "_READ_CAP"),
    ("acidcat.core.forensics.integrity", "_SCAN_CAP"),
    ("acidcat.core.forensics.lsb", "_DE_CAP"),
    ("acidcat.core.forensics.lsb", "_MAX_PCM"),
    ("acidcat.core.forensics.transforms", "_READ_CAP"),
    ("acidcat.core.forensics.triage", "_LIST_CAP"),
    ("acidcat.core.forensics.triage", "_READ_CAP"),
    ("acidcat.core.formats.bitwig", "_SCAN_CAP"),
    ("acidcat.core.formats.mp3", "_RESYNC_LIMIT"),
    ("acidcat.core.formats.ni", "_MAX_INFLATE"),
    ("acidcat.core.probe", "_MARK_LIMIT"),
    ("acidcat.core.walk.ableton", "_AMXD_MAX_CHUNKS"),
    ("acidcat.core.walk.ableton", "_ASD_READ_CAP"),
    ("acidcat.core.walk.akai", "_KGRP_CAP"),
    ("acidcat.core.walk.amiga", "_CAP"),
    ("acidcat.core.walk.base", "_FRAME_LISTING_CAP"),
    ("acidcat.core.walk.base", "_ID3_READ_CAP"),
    ("acidcat.core.walk.bfdlac", "_CHUNK_CAP"),
    ("acidcat.core.walk.bfdlac", "_READ_CAP"),
    ("acidcat.core.walk.emu", "_MAX_CHUNKS"),
    ("acidcat.core.walk.emu", "_READ_CAP"),
    ("acidcat.core.walk.emu", "_REF_CAP"),
    ("acidcat.core.walk.emu", "_TOC_LIST_CAP"),
    ("acidcat.core.walk.emu", "_VOICE_CAP"),
    ("acidcat.core.walk.emu", "_VOICE_DETAIL_CAP"),
    ("acidcat.core.walk.emu", "_ZONE_CAP"),
    ("acidcat.core.walk.flac", "_SEEKPOINT_ROW_CAP"),
    ("acidcat.core.walk.gf1pat", "_READ_CAP"),
    ("acidcat.core.walk.krz", "_OBJECT_CAP"),
    ("acidcat.core.walk.krz", "_READ_CAP"),
    ("acidcat.core.walk.labx", "_META_CAP"),
    ("acidcat.core.walk.labx", "_PRESET_CAP"),
    ("acidcat.core.walk.mp4", "_MOOV_CAP"),
    ("acidcat.core.walk.mp4", "_STCO_CAP"),
    ("acidcat.core.walk.mpc", "_PGM_PAD_CAP"),
    ("acidcat.core.walk.mpc", "_XPM_SAMPLE_CAP"),
    ("acidcat.core.walk.mpc", "_XPN_ENTRY_CAP"),
    ("acidcat.core.walk.mpc", "_XPN_XML_CAP"),
    ("acidcat.core.walk.mpc", "_XTD_CAP"),
    ("acidcat.core.walk.multisample", "_ZONE_CAP"),
    ("acidcat.core.walk.rmid", "_RMID_CAP"),
    ("acidcat.core.walk.rx2", "_MAX"),
    ("acidcat.core.walk.sf2", "_SAMPLE_LIST_CAP"),
    ("acidcat.core.walk.svx", "_READ_CAP"),
    ("acidcat.core.walk.tracker", "_SAMPLE_CAP"),
    ("acidcat.core.walk.sf2", "_SF2_CAP"),
    ("acidcat.core.walk.sigmf", "_ANNOTATION_CAP"),
    ("acidcat.core.walk.sigmf", "_EXT_KEY_CAP"),
    ("acidcat.core.walk.sigmf", "_META_CAP"),
    ("acidcat.core.walk.tracker", "_ORDER_CAP"),
    ("acidcat.core.walk.tracker", "_XREF_CAP"),
    ("acidcat.commands.inspect", "_FULL_RAW_CAP"),
    ("acidcat.commands.od", "_AUTO_DUMP_CAP"),
    ("acidcat.commands.od", "_MARK_CAP"),
}


# ── the sweep ───────────────────────────────────────────────────────

def _iff(form, body_id, chunks):
    body = body_id + b"".join(chunks)
    return form + struct.pack(">I", len(body)) + body


def _svx_many_chunks(tmp_path, n):
    """An 8SVX with n tiny chunks, to cross svx's chunk cap."""
    ch = [b"ANNO" + struct.pack(">I", 2) + b"hi" for _ in range(n)]
    p = tmp_path / "many.8svx"
    p.write_bytes(_iff(b"FORM", b"8SVX", ch))
    return str(p)


def _smus_many_chunks(tmp_path, n):
    """FORM/SMUS, which is what routes to the amiga walker. A generic FORM/ILBM
    falls through to structural triage instead, which has its own bounds and
    would have made this test assert about the wrong module."""
    ch = [b"SHDR" + struct.pack(">I", 6) + struct.pack(">HHH", 120, 100, 1)]
    ch += [b"NAME" + struct.pack(">I", 2) + b"hi" for _ in range(n)]
    p = tmp_path / "many.smus"
    p.write_bytes(_iff(b"FORM", b"SMUS", ch))
    return str(p)


SWEPT = [
    # (module that READS the constant, name, patched value, builder, says)
    ("acidcat.core.walk.svx", "_CHUNK_CAP", 4, _svx_many_chunks, "cap"),
    ("acidcat.core.walk.amiga", "_CHUNK_CAP", 4, _smus_many_chunks, "cap"),
]


@pytest.mark.parametrize("module,const,small,build,says",
                         SWEPT, ids=[f"{m.rsplit('.', 1)[-1]}.{c}"
                                     for m, c, _s, _b, _y in SWEPT])
def test_a_bound_that_bites_says_so(tmp_path, monkeypatch, module, const, small,
                                    build, says):
    """Patch the cap small, cross it, and require the walker to say so.

    Asserting on the returned warnings rather than on rendered output: whether
    the note reaches the screen is the renderer's job, tested once elsewhere,
    and a table that reflows should not break a semantic test.
    """
    from acidcat.core.walk import walk_file
    mod = importlib.import_module(module)
    assert hasattr(mod, const), (
        f"{module} does not read {const}; the registry must name the module "
        f"that READS a constant, not the one that defines it -- patching the "
        f"definition does not reach a `from x import CAP` binding")
    monkeypatch.setattr(mod, const, small)
    path = build(tmp_path, small * 4)
    _label, _chunks, warns = walk_file(path)
    assert any(says in w for w in warns), (
        f"{const} was crossed and nothing said so; warnings were {warns}")


@pytest.mark.parametrize("module,const,small,build,says",
                         SWEPT, ids=[f"{m.rsplit('.', 1)[-1]}.{c}"
                                     for m, c, _s, _b, _y in SWEPT])
def test_a_bound_that_does_not_bite_stays_quiet(tmp_path, module, const, small,
                                                build, says):
    """The control. Without it this suite passes for a walker that emits the
    note unconditionally, which is how an honest tool becomes a noisy one."""
    from acidcat.core.walk import walk_file
    path = build(tmp_path, 3)                 # well under the shipping cap
    _label, _chunks, warns = walk_file(path)
    assert not any(says in w for w in warns), (
        f"{const} was not crossed but something announced it: {warns}")


# ── the ledger ──────────────────────────────────────────────────────

def test_the_enumerator_still_matches_something():
    """Guards the guard. If the regex or the AST walk stops matching, every
    assertion below passes by finding nothing -- a green file that checked no
    code at all."""
    found = declared_bounds()
    assert len(found) > 60, (
        f"only {len(found)} bounds found; the enumerator has stopped working "
        f"and this file is asserting nothing")
    names = {n for _m, n, _l, _v in found}
    assert "_READ_CAP" in names, (
        "no _READ_CAP found: the arithmetic evaluator is broken, and byte caps "
        "spelled `64 * 1024 * 1024` are being dropped silently")


def test_every_bound_is_accounted_for():
    """A new bound must land in exactly one bucket: swept, exempt, or pending.

    This is the half that makes the file worth having. Fixing twenty sites is a
    day's work that stays fixed; what stops the twenty-first is that it cannot
    be added without someone deciding which bucket it belongs in.
    """
    swept = {(m, c) for m, c, _s, _b, _y in SWEPT}
    known = swept | set(EXEMPT) | PENDING_1_0_1
    found = {(m, n) for m, n, _l, _v in declared_bounds()}
    lines = {(m, n): line for m, n, line, _v in declared_bounds()}
    new = found - known
    assert not new, (
        "a new bounded result appeared. Register it in SWEPT, or add it to "
        "EXEMPT with a Reason, or to PENDING_1_0_1:\n  "
        + "\n  ".join(f"{m}:{lines[(m, n)]} {n}" for m, n in sorted(new)))


def test_the_ledger_has_no_ghosts():
    """The other direction, and the half people forget. A constant that no
    longer exists must leave the ledger, or the lists slowly become fiction."""
    found = {(m, n) for m, n, _l, _v in declared_bounds()}
    swept = {(m, c) for m, c, _s, _b, _y in SWEPT}
    for label, bucket in (("SWEPT", swept), ("EXEMPT", set(EXEMPT)),
                          ("PENDING_1_0_1", PENDING_1_0_1)):
        ghosts = bucket - found
        assert not ghosts, (
            f"{label} names constants that no longer exist; drop them:\n  "
            + "\n  ".join(f"{m} {n}" for m, n in sorted(ghosts)))


def test_pending_only_shrinks():
    """A ratchet, after tests/test_targets.py. The number is written down so
    that adding to the list is a visible act rather than a quiet one."""
    assert len(PENDING_1_0_1) <= 61, (
        f"PENDING_1_0_1 has grown to {len(PENDING_1_0_1)}. A new bound belongs "
        f"in SWEPT or EXEMPT; this list is debt and may only shrink.")


def test_every_exemption_gives_a_reason():
    """A Reason from the closed set, and prose that says where the behaviour is
    covered instead. An exemption with an empty reason is a skip wearing a
    costume."""
    for (mod, const), value in EXEMPT.items():
        assert isinstance(value, tuple) and len(value) == 2, (mod, const)
        reason, prose = value
        assert isinstance(reason, Reason), f"{mod}.{const} has no Reason"
        assert len(prose) > 20, f"{mod}.{const}: reason is too thin to audit"


# ── probe: the RE surface the workflow leans on hardest ─────────────

def _repeated(tmp_path, name, unit, times):
    p = tmp_path / name
    p.write_bytes(unit * times)
    return str(p)


def _probe(*args):
    import subprocess
    import sys as _sys
    return subprocess.run([_sys.executable, "-m", "acidcat", "probe", *args],
                          capture_output=True, text=True, timeout=600)


def test_probe_find_reports_the_true_hit_count(tmp_path):
    """`find_bytes` stopped at 512 and its own docstring said "every offset",
    so the command printed the cap as the number of hits. On a file with 3,000
    occurrences it said "512 hit(s)"."""
    p = _repeated(tmp_path, "many.bin", b"AB", 3000)
    r = _probe("find", "41", p)
    assert "3,000 hit(s)" in r.stderr, r.stderr
    assert "listing the first 512" in r.stderr, r.stderr


def test_probe_says_nothing_when_everything_fits(tmp_path):
    """Silence is the claim of completeness. A caveat on a result that was not
    truncated is noise, and noise is how the real ones stop being read."""
    p = _repeated(tmp_path, "few.bin", b"AB", 10)
    r = _probe("find", "41", p)
    assert "10 hit(s)" in r.stderr
    assert "listing the first" not in r.stderr, r.stderr


def test_probe_diff_counts_every_changed_range(tmp_path):
    """The loop EXITED at 256, so trailing differences were never examined and
    the printed count was the cap."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(bytes(4000))
    b.write_bytes(bytes([(i % 2) * 255 for i in range(4000)]))
    r = _probe("diff", str(a), str(b))
    assert "2,000 changed range(s)" in r.stdout, r.stdout
    assert "listing the first 256" in r.stderr, r.stderr


def test_probe_strings_reports_how_many_it_found(tmp_path):
    p = tmp_path / "s.bin"
    p.write_bytes(b"".join(b"HELLO%04d\x00" % i for i in range(1500)))
    r = _probe("strings", str(p))
    assert "1,500 string(s)" in r.stderr, r.stderr
    assert "listing the first 1,000" in r.stderr


def test_the_cap_note_never_lands_on_stdout(tmp_path):
    """stdout carries records. A note there would break `probe --json ... | jq`
    the same way a trailing summary once made validate's JSON unparseable."""
    import json as _json
    p = _repeated(tmp_path, "many.bin", b"AB", 3000)
    r = _probe("--json", "find", "41", p)
    doc = _json.loads(r.stdout)              # must parse despite the note
    assert len(doc["hits"]) == 512
    assert "listing the first" in r.stderr
