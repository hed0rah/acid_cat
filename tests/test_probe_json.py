"""`probe` is the RE surface and had no machine output at all.

All eight subverbs printed their summary and their results together on stdout,
so scripting `probe find` meant `tail -n +2 | tr -d ' '` -- and `probe read`,
the verb that recovers an offset table, could only be scraped with sed. Two
fixes, both needed: `--json` for the data, and the human summary moved to
stderr so the plain output pipes cleanly too.
"""

import json
import struct
import subprocess
import sys

import pytest


def _wav(path):
    pcm = b"\x00\x01" * 512
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    _wav(p)
    return str(p)


def _probe(wav, *args):
    """`probe SUBVERB [OPTIONS] FILE...` -- the operand goes last, so a glob
    expands into it the way strings(1) and file(1) have always accepted."""
    return subprocess.run([sys.executable, "-m", "acidcat", "probe"]
                          + list(args) + [wav], capture_output=True, text=True)


@pytest.mark.parametrize("argv,verb", [
    (["--json", "find", "s:data"], "find"),
    (["--json", "strings", "--min", "4"], "strings"),
    (["--json", "read", "0x14", "--type", "u32", "--le"], "read"),
    (["--json", "scan", "44100", "--type", "u32"], "scan"),
    (["--json", "entropy"], "entropy"),
])
def test_every_subverb_emits_json(wav, argv, verb):
    r = _probe(wav, *argv)
    doc = json.loads(r.stdout)
    assert doc["verb"] == verb


def test_find_offset_feeds_carve(wav):
    """The pipeline that previously needed sed in the middle."""
    doc = json.loads(_probe(wav, "--json", "find", "s:data").stdout)
    off = doc["hits"][0]["offset"]

    out = subprocess.run([sys.executable, "-m", "acidcat", "carve", wav,
                          "--offset", str(off), "--length", "4",
                          "--encoding", "hex"], capture_output=True, text=True)
    assert bytes.fromhex(out.stdout.replace(" ", "")) == b"data"


def test_read_returns_the_decoded_values(wav):
    doc = json.loads(_probe(wav, "--json", "read", "0x18",
                            "--type", "u32", "--le").stdout)
    assert doc["values"] == [44100]      # sample rate, 4 bytes into fmt payload


def test_summaries_go_to_stderr_so_plain_output_pipes(wav):
    """`probe find` printed '1 hit(s) for s:data' on stdout, above the offsets,
    so the plain output could not be read by anything without skipping a line."""
    r = _probe(wav, "find", "s:data")
    assert "hit(s)" in r.stderr
    assert "hit(s)" not in r.stdout
    assert r.stdout.split() == ["0x00000024"]


def test_scan_summary_also_moved(wav):
    r = _probe(wav, "scan", "44100", "--type", "u32")
    assert "hit(s)" in r.stderr and "hit(s)" not in r.stdout
    assert all(ln.strip().startswith("0x") for ln in r.stdout.splitlines() if ln.strip())


def test_json_keeps_the_exit_codes(wav):
    """--json must not turn a negative result into a success."""
    assert _probe(wav, "--json", "find", "deadbeefcafe").returncode == 1
    assert _probe(wav, "--json", "find", "s:data").returncode == 0


def test_entropy_json_carries_the_numbers_behind_the_plot(wav):
    doc = json.loads(_probe(wav, "--json", "entropy").stdout)
    assert doc["windows"] and len(doc["windows"]) == doc["window"]
    assert doc["min"] <= doc["mean"] <= doc["max"]
    assert doc["high_threshold"] == 7.2
