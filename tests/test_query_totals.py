"""A limited answer has to say what it left out.

`query --format wav` printed 50 paths and stopped. Not "50 of 382" -- just 50,
in every output format, with the JSON a bare 50-element array carrying no
marker at all. A caller could not tell a complete result from the first page of
one, which is the same defect as a scan reporting its cap as a count, aimed at
whoever is reading the output.

The total costs one extra COUNT per library. Summing across libraries is exact
rather than approximate: the registry refuses to register a library whose root
overlaps another, so a file belongs to exactly one library and cannot be
counted twice.

The trap worth recording: the first attempt computed the total from the merged
row list, which the per-library SQL had ALREADY truncated with its own LIMIT.
That produced total == count on every query -- the bug being fixed, reproduced
inside the fix. The count has to be unbounded SQL, not len() of anything.
"""

import json
import os
import pathlib
import struct
import subprocess
import sys

import pytest

N_FILES = 120


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    """A throwaway ACIDCAT_HOME with a known number of indexed files.

    Never touches the user's real registry -- the env var relocates both the
    registry and the per-library DBs.
    """
    base = tmp_path_factory.mktemp("qt")
    home, corpus = base / "home", base / "corpus"
    corpus.mkdir()
    pcm = b"\x00\x01" * 400
    body = (b"WAVE" + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    blob = b"RIFF" + struct.pack("<I", len(body)) + body
    for i in range(N_FILES):
        (corpus / f"t{i:03d}.wav").write_bytes(blob)

    env = dict(os.environ, ACIDCAT_HOME=str(home))
    r = subprocess.run([sys.executable, "-m", "acidcat", "index", str(corpus),
                        "--label", "qtest"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        pytest.skip(f"could not build a scratch index: {r.stderr[-300:]}")
    return env


def _q(env, *args):
    return subprocess.run([sys.executable, "-m", "acidcat", "query", *args],
                          capture_output=True, text=True, env=env)


def test_a_truncated_query_says_how_many_it_left_out(indexed):
    r = _q(indexed, "--format", "wav", "--paths-only")
    assert len(r.stdout.strip().splitlines()) == 50
    assert f"of {N_FILES}" in r.stderr, (
        f"no total reported; the answer is indistinguishable from a complete "
        f"one:\n{r.stderr}")


def test_a_complete_query_does_not_claim_truncation(indexed):
    """The other half. A note that fires when nothing was hidden is noise, and
    noise is how a real warning stops being read."""
    r = _q(indexed, "--format", "wav", "--paths-only", "--limit", "500")
    assert len(r.stdout.strip().splitlines()) == N_FILES
    assert "showing" not in r.stderr, r.stderr


def test_the_note_goes_to_stderr_so_stdout_stays_machine_readable(indexed):
    """--paths-only feeds xargs and --json feeds jq. The note is for the
    human; the records are for the program."""
    r = _q(indexed, "--format", "wav", "--json")
    doc = json.loads(r.stdout)          # must parse despite the note
    assert len(doc) == 50
    assert "of 120" in r.stderr


def test_the_total_is_not_computed_from_the_truncated_rows(indexed):
    """Guards the specific mistake made while writing this.

    If the total is derived from the returned rows it equals the limit, and the
    message degenerates to "showing 50 of 50" -- which reads as complete.
    """
    r = _q(indexed, "--format", "wav", "--paths-only", "--limit", "5")
    assert "showing 5 of 5" not in r.stderr
    assert f"of {N_FILES}" in r.stderr, r.stderr


# ── the MCP face, where there is no stderr to read ─────────────────

def _mcp(env, code):
    """Run handler code in a fresh interpreter.

    Not an in-process import: the MCP resolves its registry path once and
    caches it, so setting ACIDCAT_HOME inside a test that already imported the
    module reaches a connection pointed at the real registry. A subprocess is
    the only way to be sure the scratch home is the one being read.
    """
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    return json.loads(r.stdout)


def test_mcp_search_reports_the_true_match_count(indexed):
    """A model has no stderr, so truncation has to travel in the payload.

    "count" was len() of the already-truncated list, so search reported 50
    while list_formats on the same server reported 120 -- one server giving two
    answers about one corpus.
    """
    got = _mcp(indexed, """
import json
from acidcat.mcp_server import handlers as H
r = H.search_samples({'format': 'wav'})
print(json.dumps({'count': r['count'], 'total': r['total_matched'],
                  'trunc': r['truncated'], 'nsamples': len(r['samples']),
                  'lf': H.list_formats({})['formats'][0]['count']}))
""")
    assert got["count"] == 50
    assert got["total"] == N_FILES
    assert got["trunc"] is True
    assert got["lf"] == got["total"], (
        "search and list_formats still disagree about the same corpus")
    # count is deliberately unchanged: adding keys is backward compatible,
    # redefining an existing one is not
    assert got["count"] == got["nsamples"]


def test_mcp_locate_reports_the_true_match_count(indexed):
    got = _mcp(indexed, """
import json
from acidcat.mcp_server import handlers as H
a = H.locate_sample({'name': 't0'})
b = H.locate_sample({'name': 't0', 'limit': 10000})
print(json.dumps({'ac': a['count'], 'at': a['total_matched'],
                  'atr': a['truncated'], 'bc': b['count'],
                  'bt': b['total_matched'], 'btr': b['truncated']}))
""")
    assert got["atr"] is True
    assert got["at"] > got["ac"]
    assert got["btr"] is False
    assert got["bc"] == got["at"], (
        "the reported total does not match what an unlimited call returns")


# ── index must not keep its own idea of what a directory holds ──────

def test_index_walks_every_format_acidcat_knows(tmp_path_factory):
    """index kept a private 24-entry extension list while the shared set knew
    87, so it opened the .wav in a folder and passed over the .w64, .rf64,
    .bwf, .aifc and every tracker module beside it -- formats with full
    walkers. `detect` on the same folder read them fine, so the two verbs
    disagreed about what the directory contained.

    Asserted against the shared set rather than a number, so it keeps holding
    as formats are added.
    """
    from acidcat.core.catalogue.indexing import INDEXABLE_EXTENSIONS
    from acidcat.util.targets import KNOWN_EXTS
    missing = set(KNOWN_EXTS) - set(INDEXABLE_EXTENSIONS)
    assert not missing, (
        f"index would silently pass over {len(missing)} known formats: "
        f"{sorted(missing)[:12]}")


def test_index_reports_what_it_passed_over(tmp_path_factory):
    """"0 skipped" on a walk that never opened half the folder is the report
    this whole exercise exists to stop being wrong about.

    Reported separately from "skipped", which means seen-before-and-unchanged:
    that is a file which IS in the index, and folding the two together makes a
    filtered file read as already up to date.
    """
    base = tmp_path_factory.mktemp("ix")
    home, corpus = base / "home", base / "c"
    corpus.mkdir()
    src = pathlib.Path(__file__).parent.parent / "data" / "test_formats" / \
        "gs-16b-2c-44100hz.wav"
    if not src.exists():
        pytest.skip("no wav specimen")
    (corpus / "a.wav").write_bytes(src.read_bytes())
    for junk in ("readme.txt", "cover.jpg", "notes.md"):
        (corpus / junk).write_text("x")

    env = dict(os.environ, ACIDCAT_HOME=str(home))
    r = subprocess.run([sys.executable, "-m", "acidcat", "index", str(corpus),
                        "--label", "t"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "3 unrecognised" in r.stderr, (
        f"the three non-audio files left no trace:\n{r.stderr}")


def test_a_forward_version_db_is_a_message_not_a_bug_report(tmp_path_factory):
    """SchemaVersionError exists to be shown to a person.

    It means the DB was written by a newer acidcat and this build will not
    touch it -- a correct, intentional refusal. No call site caught it, so the
    user got a traceback plus "internal error (this is a bug)", which tells
    them their working setup is broken and buries the one sentence saying what
    to actually do.
    """
    base = tmp_path_factory.mktemp("sv")
    home, corpus = base / "home", base / "c"
    corpus.mkdir()
    src = pathlib.Path(__file__).parent.parent / "data" / "test_formats" / \
        "gs-16b-2c-44100hz.wav"
    if not src.exists():
        pytest.skip("no wav specimen")
    (corpus / "a.wav").write_bytes(src.read_bytes())
    env = dict(os.environ, ACIDCAT_HOME=str(home))
    r = subprocess.run([sys.executable, "-m", "acidcat", "index", str(corpus),
                        "--label", "sv"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        pytest.skip("could not build a scratch index")

    import glob
    import sqlite3
    dbs = glob.glob(str(home / "libraries" / "*.db"))
    if not dbs:
        pytest.skip("no per-library DB to age")
    conn = sqlite3.connect(dbs[0])
    try:
        conn.execute("UPDATE meta SET v='9' WHERE k='schema_version'")
        conn.commit()
    except sqlite3.Error:
        pytest.skip("meta table shape differs")
    finally:
        conn.close()

    for argv in (["index", "--stats", "sv"],
                 ["index", str(corpus), "--label", "sv"]):
        r = subprocess.run([sys.executable, "-m", "acidcat", *argv],
                           capture_output=True, text=True, env=env)
        assert "Traceback" not in r.stderr, f"{argv}:\n{r.stderr}"
        assert "this is a bug" not in r.stderr, f"{argv}:\n{r.stderr}"
        assert "schema_version 9" in r.stderr, f"{argv}:\n{r.stderr}"
        assert r.returncode == 2, f"{argv}: rc={r.returncode}"
