"""ID3v2 declared-padding cavities: the lab plants, the base finds.

An ID3v2 tag is "ID3", version, flags, then a four-byte SYNCSAFE size giving
the length of the tag body. Players read exactly that many bytes and jump to
the MPEG audio after it. Taggers leave a run of ZERO padding at the end of the
body so a tag can grow without rewriting the file, and that padding is
declared, inside the tag, and never rendered.

So non-zero padding is a cavity of the cleanest kind: the size field stays
honest, the audio is untouched, the file plays identically, and no conformant
tagger has any reason to write a non-zero byte there.

Already detected before this test existed, by a rule named exactly what the
planter proposed -- `id3_padding_nonzero`. Measured at 128, 512, 1024 and 4096
bytes, all reported. Three cavity vectors have now been measured against
acidcat before writing anything and all three were already covered; the
detection half of this migration turns out to have happened already, and what
is missing is the evidence.

NO SIZE FLOOR HERE, unlike RIFF JUNK. The trade differs by format: real DAWs
write small non-zero runs into JUNK, so a floor there buys signal. The ID3 spec
says padding is zero and taggers honour it, so there is no measured noise to
trade against and any non-zero byte is worth saying.
"""

import os

import pytest

from acidcat.core.forensics import anomalies, cavity
from acidcat.core.walk import walk_file
from conftest import CORPUS_MP3

id3 = pytest.importorskip("acidcat_lab.cavity.id3",
                          reason="acidcat_lab not installed")

PAYLOAD = b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG. " * 24


@pytest.fixture
def carrier():
    if not os.path.isfile(CORPUS_MP3):
        pytest.skip("no carrier MP3 available")
    with open(CORPUS_MP3, "rb") as fh:
        return fh.read()


def _scan(tmp_path, blob, name="x.mp3"):
    p = tmp_path / name
    p.write_bytes(blob)
    label, chunks, warns = walk_file(str(p))
    findings = anomalies.scan(str(p), label, chunks, warns) or []
    return ({f["rule"] for f in findings},
            cavity.account(str(p), label, chunks), warns)


class TestTheLoop:
    def test_a_payload_in_declared_padding_is_found(self, tmp_path, carrier):
        rules, _rep, _w = _scan(tmp_path, id3.embed(carrier, PAYLOAD))
        assert "id3_padding_nonzero" in rules, rules

    def test_the_carrier_it_was_planted_in_is_clean(self, tmp_path, carrier):
        rules, rep, warns = _scan(tmp_path, carrier)
        assert "id3_padding_nonzero" not in rules, rules
        assert rep["regions"] == [] and not warns

    def test_the_payload_survives_a_round_trip(self, carrier):
        """Pins that the fixture really carries data. A planter that silently
        dropped it would satisfy the assertions above."""
        assert id3.extract(id3.embed(carrier, PAYLOAD)) == PAYLOAD

    def test_the_audio_after_the_tag_is_untouched(self, tmp_path, carrier):
        """The reason this vector works at all. The payload sits BEFORE the
        audio and is counted by the tag length, so every player skips it and
        the stream that follows is byte-identical."""
        planted = id3.embed(carrier, PAYLOAD)
        p = tmp_path / "planted.mp3"
        p.write_bytes(planted)
        label, chunks, warns = walk_file(str(p))
        rate = next((f.get("value") for c in chunks
                     for f in c.get("fields", [])
                     if f.get("name") == "sample_rate"), None)
        assert rate == 44100, (rate, label)
        assert not warns, warns


class TestSize:
    @pytest.mark.parametrize("n", [128, 512, 1024, 4096])
    def test_reported_at_every_size(self, tmp_path, carrier, n):
        """No floor, and that is a per-format judgement rather than an
        oversight: the spec says this region is zero and taggers honour it."""
        rules, _rep, _w = _scan(tmp_path, id3.embed(carrier, b"S" * n))
        assert "id3_padding_nonzero" in rules, (n, rules)


class TestCoverageIsNotEnough:
    def test_a_planted_file_stays_fully_accounted_for(self, tmp_path, carrier):
        """Every byte is inside the declared tag length. A detector that only
        reported unexplained bytes would call this clean and be entirely right
        about coverage while being entirely wrong about the file."""
        _rules, rep, warns = _scan(tmp_path, id3.embed(carrier, PAYLOAD))
        assert rep["coverage"] >= 0.9999, rep["coverage"]
        assert rep["regions"] == [], rep["regions"]
        assert not warns, warns
