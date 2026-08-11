"""`--force` and `--resync` must have a machine face.

Both printed the human table verbatim under `--json` -- not "prose inside JSON
fields", but no JSON at all, so `jq` got a parse error. These are the two verbs
you reach for on a file nothing walks, and their output is exactly what you want
to loop over: `--force` produces `--format <id>` leads, `--resync` produces
carve ranges. Plain `--json` on a supported file worked fine, so the gap was two
code paths returning before the emitter.
"""

import json
import struct
import subprocess
import sys

import pytest


def _run(*args):
    return subprocess.run([sys.executable, "-m", "acidcat"] + list(args),
                          capture_output=True, text=True)


@pytest.fixture
def opaque(tmp_path):
    p = tmp_path / "mystery.ch1"
    p.write_bytes(bytes((i * 31 + 7) % 256 for i in range(4096)))
    return str(p)


@pytest.fixture
def damaged(tmp_path):
    pcm = b"\x00\x01" * 4000
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    raw = bytearray(b"RIFF" + struct.pack("<I", len(body)) + body)
    raw[0:4] = b"XXXX"                        # smash the magic
    struct.pack_into("<I", raw, 16, 0xDEAD)   # and a chunk size
    p = tmp_path / "damaged.wav"
    p.write_bytes(bytes(raw))
    return str(p)


def test_force_json_is_json(opaque):
    r = _run("inspect", "--force", "--json", opaque)
    doc = json.loads(r.stdout)              # the whole point
    assert doc["mode"] == "force"
    assert doc["identified"] is False       # never claims an identification
    assert doc["candidates"]
    c = doc["candidates"][0]
    assert {"format", "chunks", "fields", "plausible", "complaint"} <= set(c)


def test_force_json_leads_feed_back_in(opaque):
    """A lead is only useful if `--format <id>` accepts it."""
    doc = json.loads(_run("inspect", "--force", "--json", opaque).stdout)
    ids = {c["format"] for c in doc["candidates"]}
    known = _run("formats", "--json").stdout
    assert ids <= {f["id"] for f in json.loads(known)}


def test_resync_json_is_json_and_carries_carve_ranges(damaged):
    r = _run("inspect", "--resync", "--json", damaged)
    doc = json.loads(r.stdout)
    assert doc["mode"] == "resync"
    assert doc["chunks"]
    c = doc["chunks"][0]
    assert c["payload_offset"] == c["offset"] + 8

    # the range must actually carve
    out = _run("carve", damaged, "--offset", str(c["payload_offset"]),
               "--length", str(min(c["size"], 32)), "--encoding", "hex")
    assert out.returncode == 0 and out.stdout.strip()


def test_human_output_is_unchanged(opaque, damaged):
    """The tables are the best output in the tool; --json is additive."""
    assert "hypotheses, not identifications" in _run(
        "inspect", "--force", opaque).stdout
    assert "recovered" in _run("inspect", "--resync", damaged).stdout
