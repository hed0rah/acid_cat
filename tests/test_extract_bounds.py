"""`extract` must not count a sample the file does not contain.

A MOD whose 4th sample header declares 4,096 bytes starting at EOF was listed as
`0003_smp3.wav  4,096 B 8-bit`, counted in "extracted 4 sample(s)", exit 0 --
and written to disk as a 44-byte WAV header with a zero-length data chunk. The
declared size was reported as though it were the recovered size.

`audit` and `inspect --anomalies` flag the same truncation correctly on the same
file, so the information was available; the extractor just sliced unchecked.
"""

import json
import math
import struct
import subprocess
import sys

import pytest

from acidcat.core.extract.samples import iter_samples

_HDR = 20 + 31 * 30 + 1 + 1 + 128 + 4        # MOD header through the M.K. magic


def _mod(tmp_path, real=3, liar_words=2048):
    """A 1-pattern MOD with `real` 256-byte samples plus one declaring
    `liar_words` words of data that is not in the file."""
    out = bytearray(b"truncbomb".ljust(20, b"\x00"))
    for i in range(31):
        if i < real:
            words = 128
        elif i == real:
            words = liar_words
        else:
            words = 0
        out += f"smp{i}".encode().ljust(22, b"\x00")
        out += struct.pack(">H", words) + bytes([0, 64]) + struct.pack(">HH", 0, 0)
    out += bytes([1, 127]) + bytes(128) + b"M.K." + bytes(1024)
    for _ in range(real):
        out += bytes((int(100 * math.sin(j / 6.0)) & 0xFF) for j in range(256))
    p = tmp_path / "trunc.mod"
    p.write_bytes(bytes(out))
    return p


def test_past_eof_sample_is_not_counted(tmp_path):
    mod = _mod(tmp_path)
    recs = list(iter_samples(str(mod)))

    extracted = [r for r in recs if r.get("wav")]
    notes = [r["note"] for r in recs if not r.get("wav")]

    assert len(extracted) == 3, "a sample with no bytes in the file is not a sample"
    assert len(notes) == 1
    assert "declared 4,096 B" in notes[0]
    assert "not extracted" in notes[0]


def test_reported_size_is_recovered_not_declared(tmp_path):
    """Even a partially-present sample must report what was recovered."""
    mod = _mod(tmp_path, real=3, liar_words=0)
    # truncate mid-way through the last sample: 128 of its 256 bytes survive
    raw = bytearray(mod.read_bytes())
    del raw[-128:]
    mod.write_bytes(bytes(raw))

    recs = [r for r in iter_samples(str(mod)) if r.get("wav")]
    assert len(recs) == 3
    assert "128 B 8-bit" in recs[-1]["note"]
    assert "declared 256 B" in recs[-1]["note"]
    assert "128 B past EOF" in recs[-1]["note"]


def test_cli_count_and_disk_agree(tmp_path):
    """The summary line, the JSON manifest, and the files on disk must all be
    the same number. They were 4, 4, and 3-plus-an-empty-header."""
    mod = _mod(tmp_path)
    out = tmp_path / "ex"

    r = subprocess.run([sys.executable, "-m", "acidcat", "extract", str(mod),
                        "-o", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "extracted 3 sample(s)" in r.stdout + r.stderr

    on_disk = sorted(p for p in out.iterdir() if p.suffix == ".wav")
    assert len(on_disk) == 3
    assert all(p.stat().st_size > 44 for p in on_disk), "a header with no audio"

    # --json is a manifest instead of writing files (its help says so)
    j = subprocess.run([sys.executable, "-m", "acidcat", "extract", str(mod),
                        "--json"], capture_output=True, text=True)
    doc = json.loads(j.stdout)
    assert len(doc["samples"]) == 3
    assert doc["notes"] and "not extracted" in doc["notes"][0]
