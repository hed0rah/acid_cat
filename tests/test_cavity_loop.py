"""The lab plants, the base finds, and CI fails if it does not.

This is the test the two-package split exists to make possible. Every other
test here checks acidcat against a fixture someone wrote by hand, which checks
acidcat against one person's idea of what a hidden payload looks like. Here the
adversary is code: `acidcat_lab` constructs the file and `acidcat` has to find
it. It runs in one suite because they live in one repo; split across two, this
would be a version pin and an intention.

A CORRECTION THIS FILE EXISTS TO PIN. The first version was written on the
belief that acidcat could not see a JUNK cavity at all. It can, and has been
able to since before any of this: `anomalies` rule 5 reports non-zero bytes in
a spec-ignorable region. The belief came from one test with a 364-byte payload,
which sits under a 1 KB floor that was calibrated on 2,328 real WAVs where
innocent non-zero JUNK topped out at 641 bytes.

So the threshold is the thing worth testing. It is a deliberate trade -- below
it, ordinary DAW metadata would light up on a large share of real files and
everyone would learn to ignore the rule -- and a trade that nothing pins is a
number that drifts.
"""

import os

import pytest

from acidcat.core.forensics import anomalies, cavity
from acidcat.core.walk import walk_file
from conftest import CORPUS_WAV

junk = pytest.importorskip("acidcat_lab.cavity.junk",
                           reason="acidcat_lab not installed")

# Comfortably over the 1 KB floor: this test is about whether the loop closes,
# not about where the boundary sits. TestTheFloor below owns that.
PAYLOAD = b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG. " * 48


@pytest.fixture
def carrier():
    if not os.path.isfile(CORPUS_WAV):
        pytest.skip("no carrier WAV available")
    with open(CORPUS_WAV, "rb") as fh:
        return fh.read()


def _findings(tmp_path, blob, name="x.wav"):
    p = tmp_path / name
    p.write_bytes(blob)
    label, chunks, warns = walk_file(str(p))
    return (anomalies.scan(str(p), label, chunks, warns) or [],
            cavity.account(str(p), label, chunks), warns)


def _rules(findings):
    return {f["rule"] for f in findings}


# ── the loop ────────────────────────────────────────────────────────

class TestTheLoop:
    def test_a_planted_cavity_is_found(self, tmp_path, carrier):
        findings, _rep, _w = _findings(tmp_path, junk.embed(carrier, PAYLOAD))
        assert "cavity_content" in _rules(findings), (
            "the lab planted %d bytes in a JUNK chunk and nothing reported it: "
            "%r" % (len(PAYLOAD), _rules(findings)))

    def test_the_carrier_it_was_planted_in_is_clean(self, tmp_path, carrier):
        """Without this the test above proves only that something is always
        reported. The same file, untouched, must come back with nothing."""
        findings, rep, _w = _findings(tmp_path, carrier)
        assert "cavity_content" not in _rules(findings), findings
        assert rep["regions"] == [], rep["regions"]

    def test_the_planted_file_is_still_a_working_wav(self, tmp_path, carrier):
        """The reason a cavity is worth detecting. If planting broke the file
        every ordinary reader would reject it and there would be nothing to
        hide in."""
        import wave
        p = tmp_path / "planted.wav"
        p.write_bytes(junk.embed(carrier, PAYLOAD))
        with wave.open(str(p)) as w:
            assert w.getnframes() > 0 and w.getframerate() > 0

    def test_the_payload_survives_a_round_trip(self, tmp_path, carrier):
        """Pins that the fixture really carries data. A planter that silently
        dropped the payload would satisfy every assertion above."""
        assert junk.extract(junk.embed(carrier, PAYLOAD)) == PAYLOAD

    def test_the_detection_does_not_lean_on_the_planter_s_marker(self, tmp_path,
                                                                 carrier):
        """`junk.embed` stamps ACJK so `extract` can find its own work again.
        Nothing obliges an adversary to do that, and a rule keyed to it would
        catch only this repo's output."""
        findings, _rep, _w = _findings(tmp_path, _raw_junk(carrier, PAYLOAD))
        assert "cavity_content" in _rules(findings), _rules(findings)


def _raw_junk(carrier, content):
    """A JUNK chunk carrying exactly `content` and no marker."""
    import struct
    body = content + (b"\x00" if len(content) % 2 else b"")
    chunk = b"JUNK" + struct.pack("<I", len(content)) + body
    out = bytearray(carrier)
    out[12:12] = chunk
    struct.pack_into("<I", out, 4, struct.unpack_from("<I", out, 4)[0] + len(chunk))
    return bytes(out)


# ── the trade, pinned ───────────────────────────────────────────────

class TestTheFloor:
    """A 1 KB floor on JUNK is a deliberate trade against real-world noise, and
    a trade nothing pins is a number that drifts."""

    def test_a_payload_sized_run_is_reported(self, tmp_path, carrier):
        findings, _rep, _w = _findings(tmp_path, _raw_junk(carrier, b"S" * 2048))
        assert "cavity_content" in _rules(findings)

    def test_daw_sized_metadata_is_not(self, tmp_path, carrier):
        """Calibrated on 2,328 real WAVs, innocent non-zero JUNK topped out at
        641 bytes. Reporting at that size would put a finding on a large share
        of ordinary files and teach everyone to ignore the rule."""
        findings, _rep, _w = _findings(tmp_path, _raw_junk(carrier, b"S" * 512))
        assert "cavity_content" not in _rules(findings), (
            "the floor has moved below the measured noise level")


# ── accounting: the part geometry can answer ────────────────────────

class TestAccounting:
    def test_a_junk_cavity_leaves_the_file_fully_accounted_for(self, tmp_path,
                                                              carrier):
        """Why coverage is not enough on its own, and why rule 5 has to look at
        content rather than at geometry.

        Every byte IS explained: the chunk is well-formed, the RIFF size is
        honest, nothing warns. A detector that only reported unexplained bytes
        would call this file clean and be entirely right about coverage while
        being entirely wrong about the file.
        """
        _f, rep, warns = _findings(tmp_path, junk.embed(carrier, PAYLOAD))
        assert rep["coverage"] >= 0.9999, rep["coverage"]
        assert rep["regions"] == [], rep["regions"]
        assert not warns, warns

    def test_appended_bytes_are_unaccounted(self, tmp_path, carrier):
        """The crude version of the same idea, and the one geometry can see:
        bytes after the container are outside every declaration."""
        findings, rep, warns = _findings(tmp_path, carrier + PAYLOAD)
        assert [r for r in rep["regions"] if r["kind"] == "unaccounted"]
        assert rep["regions"][0]["length"] == len(PAYLOAD), rep["regions"][0]
        assert rep["coverage"] < 1.0
        assert "unaccounted_bytes" in _rules(findings)
        assert warns, "appending past the declared size should already warn"

    def test_an_untrustworthy_extent_claims_nothing(self, tmp_path, carrier):
        """The bug that shaped this.

        Appended data reparses as a chunk declaring a nonsense size -- 1.4 GB
        inside a 44 KB file. Clamping that to the file end let it cover exactly
        the bytes it was appended as, and the accounting reported full coverage
        of a file it had not explained.
        """
        _f, rep, _w = _findings(tmp_path, carrier + PAYLOAD)
        assert rep["accounted"] < rep["size"], (
            "a chunk with invalid geometry was allowed to claim bytes")

    def test_padding_alone_is_not_a_region(self, tmp_path, carrier):
        """A file padded out to a boundary is padded, not carrying something."""
        for fill in (b"\x00", b" ", b"\xff"):
            _f, rep, _w = _findings(tmp_path, carrier + fill * 4096)
            assert rep["regions"] == [], (fill, rep["regions"])

    def test_a_uniform_run_is_not_padding_by_itself(self, tmp_path, carrier):
        """The bug that tightened the rule.

        4,096 repetitions of "S" appended to a WAV is perfectly uniform and
        perfectly obviously not padding, and an earlier version of this filter
        threw it away for being uniform. Padding is a byte nobody chose for its
        content, not merely a byte that repeats.
        """
        _f, rep, _w = _findings(tmp_path, carrier + b"S" * 4096)
        hits = [r for r in rep["regions"] if r["kind"] == "unaccounted"]
        assert hits and hits[0]["length"] == 4096, (
            "a uniform run of a non-padding byte was filtered as padding: %r"
            % rep["regions"])
