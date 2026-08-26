"""FLAC metadata cavities: the lab plants, the base finds.

The second pair across the boundary, and unlike the first it adds no detector.
Both vectors were already covered before this file existed, at every size
measured:

    APPLICATION block  ->  rule `application_block`
    non-zero PADDING   ->  rule `cavity_content`

That was measured before anything was written, which is the only reason a third
redundant rule did not get built. The first cavity pair was designed on the
belief that acidcat was blind to a JUNK chunk, and it was not -- the belief came
from a single undersized test payload.

So what this adds is not capability but EVIDENCE: existing behaviour pinned
against a real adversary rather than against a fixture someone wrote by hand.
A rule with no test is a rule that works until someone refactors near it.

A FLAC stream is "fLaC" then a chain of METADATA_BLOCKs then audio frames.
Decoders play the audio whatever optional metadata is present, so both vectors
leave a completely conformant file: frames honest, every length field honest,
plays everywhere. Neither shows up as damage, and neither leaves an unaccounted
byte -- coverage stays 1.0. That is precisely why the rules have to look at
CONTENT and cannot be derived from geometry.
"""

import os

import pytest

from acidcat.core.forensics import anomalies, cavity
from acidcat.core.walk import walk_file
from conftest import CORPUS_FLAC

flac = pytest.importorskip("acidcat_lab.cavity.flac",
                           reason="acidcat_lab not installed")

PAYLOAD = b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG. " * 24


@pytest.fixture
def carrier():
    if not os.path.isfile(CORPUS_FLAC):
        pytest.skip("no carrier FLAC available")
    with open(CORPUS_FLAC, "rb") as fh:
        return fh.read()


def _scan(tmp_path, blob, name="x.flac"):
    p = tmp_path / name
    p.write_bytes(blob)
    label, chunks, warns = walk_file(str(p))
    findings = anomalies.scan(str(p), label, chunks, warns) or []
    return ({f["rule"] for f in findings},
            cavity.account(str(p), label, chunks), warns)


# ── the loop ────────────────────────────────────────────────────────

class TestTheLoop:
    def test_an_application_block_payload_is_found(self, tmp_path, carrier):
        rules, _rep, _w = _scan(tmp_path, flac.embed_app(carrier, PAYLOAD))
        assert "application_block" in rules, rules

    def test_a_non_zero_padding_payload_is_found(self, tmp_path, carrier):
        """PADDING is spec'd to be zero, which makes any other byte in it the
        FLAC analogue of a non-zero RIFF JUNK chunk."""
        rules, _rep, _w = _scan(tmp_path, flac.embed_pad(carrier, PAYLOAD))
        assert "cavity_content" in rules, rules

    def test_the_carrier_they_were_planted_in_is_clean(self, tmp_path, carrier):
        """Without this the two above prove only that something is always
        reported."""
        rules, rep, warns = _scan(tmp_path, carrier)
        assert not (rules & {"application_block", "cavity_content"}), rules
        assert rep["regions"] == [] and not warns

    @pytest.mark.parametrize("plant", ["app", "pad"])
    def test_the_planted_file_is_still_a_working_flac(self, tmp_path, carrier,
                                                      plant):
        """The reason a cavity is worth detecting. If planting broke the file,
        every decoder would reject it and there would be nothing to hide in."""
        blob = (flac.embed_app if plant == "app" else flac.embed_pad)(
            carrier, PAYLOAD)
        p = tmp_path / "planted.flac"
        p.write_bytes(blob)
        label, chunks, warns = walk_file(str(p))
        assert "FLAC" in str(label).upper(), label
        assert not warns, warns
        assert any(str(c.get("id", "")).upper() == "STREAMINFO" for c in chunks)

    @pytest.mark.parametrize("plant", ["app", "pad"])
    def test_the_payload_survives_a_round_trip(self, carrier, plant):
        """Pins that the fixture really carries data. A planter that silently
        dropped the payload would satisfy every assertion above."""
        blob = (flac.embed_app if plant == "app" else flac.embed_pad)(
            carrier, PAYLOAD)
        assert flac.extract(blob) == PAYLOAD


# ── why geometry cannot answer this ─────────────────────────────────

class TestCoverageIsNotEnough:
    @pytest.mark.parametrize("plant", ["app", "pad"])
    def test_a_planted_file_stays_fully_accounted_for(self, tmp_path, carrier,
                                                      plant):
        """Both vectors leave every byte explained by a well-formed block.

        A detector that only reported unexplained bytes would call these files
        clean and be entirely correct about coverage while being entirely wrong
        about the file. Coverage is necessary and it is not sufficient, and this
        is the case that shows why.
        """
        blob = (flac.embed_app if plant == "app" else flac.embed_pad)(
            carrier, PAYLOAD)
        _rules, rep, warns = _scan(tmp_path, blob)
        assert rep["coverage"] >= 0.9999, rep["coverage"]
        assert rep["regions"] == [], rep["regions"]
        assert not warns, warns


# ── no floor here, and that is deliberate ───────────────────────────

class TestSize:
    @pytest.mark.parametrize("n", [256, 1023, 1024, 4096])
    def test_padding_is_reported_at_every_size(self, tmp_path, carrier, n):
        """RIFF JUNK carries a 1 KB floor because real DAWs put small non-zero
        runs there and flagging them would bury the signal. FLAC PADDING has no
        such population: the spec says zero, so any non-zero byte is worth
        saying, and there is no measured noise to trade against.
        """
        rules, _rep, _w = _scan(tmp_path, flac.embed_pad(carrier, b"S" * n))
        assert "cavity_content" in rules, (n, rules)
