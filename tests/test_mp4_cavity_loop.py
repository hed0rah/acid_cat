"""MP4 mdat coverage cavities, and a variable that shadowed the file size.

The third pair, and like the second it adds no detector: `mp4_mdat_coverage`
already reports mdat bytes no sample table references. An ISO-BMFF file
declares where its audio lives in `stsz`/`stco`, and a decoder reads only what
those point at, so bytes inside `mdat` that no sample references are never
touched by anything and the file plays identically.

WHAT THIS FOUND. Adding a rule that consumes `size` after the MP4 rule exposed
a variable in `anomalies.scan` that shadowed the file's length with a box's.
That fix and its regression tests live in tests/test_scan_scope.py -- it is a
scoping defect that happens to have been found here, not a property of MP4
cavities, and putting it in both places would mean two tests drifting apart
over one fact.
"""

import os

import pytest

from acidcat.core.forensics import anomalies, cavity
from acidcat.core.walk import walk_file
from conftest import CORPUS_M4A

mp4 = pytest.importorskip("acidcat_lab.cavity.mp4",
                          reason="acidcat_lab not installed")

PAYLOAD = b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG. " * 24


@pytest.fixture
def carrier():
    if not os.path.isfile(CORPUS_M4A):
        pytest.skip("no carrier M4A available")
    with open(CORPUS_M4A, "rb") as fh:
        return fh.read()


def _scan(tmp_path, blob, name="x.m4a"):
    p = tmp_path / name
    p.write_bytes(blob)
    label, chunks, warns = walk_file(str(p))
    findings = anomalies.scan(str(p), label, chunks, warns) or []
    return findings, cavity.account(str(p), label, chunks)


# ── the loop ────────────────────────────────────────────────────────

class TestTheLoop:
    def test_an_mdat_cavity_is_found(self, tmp_path, carrier):
        findings, _rep = _scan(tmp_path, mp4.embed(carrier, PAYLOAD))
        assert "mp4_mdat_coverage" in {f["rule"] for f in findings}

    def test_the_carrier_it_was_planted_in_is_clean(self, tmp_path, carrier):
        findings, rep = _scan(tmp_path, carrier)
        assert findings == [], findings
        assert rep["regions"] == [], rep["regions"]

    def test_the_planted_file_still_walks_as_mp4(self, tmp_path, carrier):
        p = tmp_path / "planted.m4a"
        p.write_bytes(mp4.embed(carrier, PAYLOAD))
        label, chunks, _w = walk_file(str(p))
        assert any(str(c.get("id", "")).strip() == "mdat" for c in chunks)
        assert "MP4" in str(label).upper() or "M4A" in str(label).upper(), label
