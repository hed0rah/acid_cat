"""Every verb that produces records can hand them to a machine.

An interface audit across all 29 verbs found six different output-format sets
and twelve verbs with no machine output at all. Two of those twelve were the
worst offenders for their own job:

  `shape` IS the data verb -- its entire output is records built for
  `sort | uniq -c` -- and TSV was hardcoded with no way to reach a JSON
  consumer, unlike its sibling `census`.

  `validate` is the CI-gate verb. You could branch on its exit code but not read
  WHICH file failed or WHY without scraping the human table.

The rule applied: flat records get all four renderings; nested or structural
output (inspect's chunk tree, dump's hex) keeps table+json. Defaults do not
change -- shape stays TSV because `sort | uniq -c` depends on it.
"""

import csv
import io
import json
import struct
import subprocess
import sys

import pytest


def _wav(path, riff_size=None):
    pcm = b"\x11\x22" * 256
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    raw = bytearray(b"RIFF" + struct.pack("<I", len(body)) + body)
    if riff_size is not None:
        struct.pack_into("<I", raw, 4, riff_size)
    path.write_bytes(bytes(raw))
    return path


def _run(*args):
    return subprocess.run([sys.executable, "-m", "acidcat"] + list(args),
                          capture_output=True, text=True)


@pytest.fixture
def files(tmp_path):
    return _wav(tmp_path / "ok.wav"), _wav(tmp_path / "bad.wav", riff_size=99999)


# ── shape ──────────────────────────────────────────────────────────

def test_shape_tsv_is_still_the_default_and_headerless(files):
    """`sort | uniq -c` counts every line, so a header row would corrupt the
    histogram. TSV must stay default AND stay headerless."""
    ok, _ = files
    r = _run("shape", "--no-path", str(ok))
    assert r.returncode == 0
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "\t" in lines[0]
    assert not lines[0].startswith("format")        # no header


@pytest.mark.parametrize("flag", ["--json", "--csv", "--output-format", "tsv"])
def test_shape_accepts_every_declared_format(files, flag):
    ok, _ = files
    args = ["shape", "--no-path", str(ok)]
    args += [flag] if flag != "--output-format" else ["--output-format", "table"]
    assert _run(*args).returncode == 0


def test_shape_json_is_parseable_records(files):
    ok, _ = files
    doc = json.loads(_run("shape", "--no-path", "--json", str(ok)).stdout)
    assert len(doc) == 1
    assert doc[0]["format"] == "RIFF/WAVE"
    assert doc[0]["chunks"] == "data,fmt"


def test_shape_csv_has_a_header(files):
    """The opposite of TSV: csv is for spreadsheets and consumers that expect
    a header, so it gets one."""
    ok, _ = files
    rows = list(csv.DictReader(io.StringIO(
        _run("shape", "--no-path", "--csv", str(ok)).stdout)))
    assert rows and rows[0]["format"] == "RIFF/WAVE"


# ── validate ───────────────────────────────────────────────────────

def test_validate_json_says_which_file_and_why(files):
    ok, bad = files
    r = _run("validate", str(ok), str(bad), "--json")
    doc = json.loads(r.stdout)

    by_status = {d["status"]: d for d in doc}
    assert by_status["ok"]["path"].endswith("ok.wav")
    assert by_status["fail"]["path"].endswith("bad.wav")
    assert by_status["fail"]["issues"] == 1
    assert by_status["fail"]["repairable"] is True
    assert by_status["fail"]["violations"][0]["describe"]


def test_validate_json_distinguishes_skipped_from_clean(tmp_path):
    """"checked and clean" and "never modelled" are different facts, and the
    exit code already treats them differently (0 vs 2)."""
    _wav(tmp_path / "a.wav")
    (tmp_path / "b.bin").write_bytes(bytes(range(256)))
    doc = json.loads(_run("validate", str(tmp_path / "a.wav"),
                          str(tmp_path / "b.bin"), "--json").stdout)
    assert {d["status"] for d in doc} == {"ok", "skipped"}


def test_validate_machine_output_is_not_polluted_by_the_summary(files):
    """The human tail ("1 of 2 file(s) have structural issues") made the JSON
    unparseable with "Extra data". stdout belongs to the records."""
    ok, bad = files
    r = _run("validate", str(ok), str(bad), "--json")
    json.loads(r.stdout)                        # must not raise
    assert "structural issues" in r.stderr


def test_validate_csv_flattens_the_violations(files):
    """A nested list has no honest cell representation; csv/tsv drop the key
    rather than stringify it into something nobody can parse."""
    ok, bad = files
    rows = list(csv.DictReader(io.StringIO(
        _run("validate", str(ok), str(bad), "--csv").stdout)))
    assert "violations" not in rows[0]
    assert rows[1]["status"] == "fail" and rows[1]["issues"] == "1"
    assert rows[1]["detail"]


def test_validate_exit_codes_survive_every_rendering(files):
    """The gate must gate the same way whichever format you asked for."""
    ok, bad = files
    for fmt in ([], ["--json"], ["--csv"], ["--output-format", "tsv"]):
        assert _run("validate", str(ok), *fmt).returncode == 0, fmt
        assert _run("validate", str(ok), str(bad), *fmt).returncode == 1, fmt


def test_validate_table_output_is_unchanged(files):
    ok, bad = files
    out = _run("validate", str(ok), str(bad)).stdout
    assert "OK    ok.wav" in out
    assert "FAIL  bad.wav" in out
