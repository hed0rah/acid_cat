"""The lab plants, the base finds, and CI fails if it does not.

This is the test the two-package split exists to make possible. Every other
test here checks acidcat against a fixture someone wrote by hand, which checks
it against one person's idea of what a hidden payload looks like. Here the
adversary is code: `acidcat_lab` constructs the file and `acidcat` has to find
it. It runs in one suite because they live in one repo; split across two, this
would be a version pin and an intention.

FOUR VECTORS, ONE TABLE. These began as four files that had converged on the
same six assertions differing only by carrier and rule name -- which is a
parameter, not a module. Adding the fifth format should be one row in VECTORS,
and anything that genuinely cannot be a row belongs in the per-format section
at the bottom rather than in a new file.

NONE OF THIS ADDS DETECTION. All four vectors were measured against acidcat
before anything was written and all four were already covered. The ID3 rule is
even named what the planter proposed for it. What was missing is evidence: a
rule with no adversarial test is a rule that works until somebody refactors
near it.

That habit came from getting it wrong. The first pair was built believing
acidcat was blind to a JUNK chunk, which came from one undersized test payload.
Three redundant rules did not get written because the measurement came first
the next three times.

WHY COVERAGE IS NOT THE TEST. Every vector here leaves the file at coverage
1.0 -- each payload sits inside a well-formed structure that accounts for it,
nothing warns, and the file plays everywhere. A detector reporting only
unexplained bytes would call all four clean and be entirely right about
coverage while being entirely wrong about the file. That is why these rules
read CONTENT, and it is asserted per vector rather than described here.
"""

import os
import struct
import wave

import pytest

from acidcat.core.forensics import anomalies, cavity
from acidcat.core.walk import walk_file
from conftest import CORPUS_FLAC, CORPUS_M4A, CORPUS_MP3, CORPUS_WAV

lab = pytest.importorskip("acidcat_lab.cavity",
                          reason="acidcat_lab not installed")

PAYLOAD = b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG. " * 48


class Vector:
    """One hiding place: where to plant, what should notice, what is normal.

    `floor` is the payload size below which the rule deliberately stays quiet,
    or None where there is no such trade. It differs per format and is a
    judgement rather than an oversight, so it is carried here and asserted
    rather than left to be rediscovered.
    """

    def __init__(self, name, carrier, plant, rule, floor=None, extract=None):
        self.name = name
        self.carrier = carrier
        self.plant = plant
        self.rule = rule
        self.floor = floor
        self.extract = extract

    def __repr__(self):
        return self.name


VECTORS = [
    # RIFF JUNK is spec'd as ignorable padding, and in the RF64/BW64 world it
    # is the reserved placeholder that becomes ds64 past 4 GB -- so until then
    # its content is free bytes and a conformant reader skips them.
    Vector("wav-junk", CORPUS_WAV, lambda c, p: lab.junk.embed(c, p),
           "cavity_content", floor=1024, extract=lambda b: lab.junk.extract(b)),
    # A FLAC APPLICATION block carries a 4-byte id plus free data, and decoders
    # skip ids they do not know.
    Vector("flac-application", CORPUS_FLAC, lambda c, p: lab.flac.embed_app(c, p),
           "application_block", extract=lambda b: lab.flac.extract(b)),
    # The FLAC spec says PADDING is zero, which makes any other byte in it the
    # analogue of a non-zero JUNK chunk.
    Vector("flac-padding", CORPUS_FLAC, lambda c, p: lab.flac.embed_pad(c, p),
           "cavity_content", extract=lambda b: lab.flac.extract(b)),
    # ID3v2 declares its own length, and taggers leave zero padding at the end
    # of the body so a tag can grow without rewriting the file.
    Vector("id3-padding", CORPUS_MP3, lambda c, p: lab.id3.embed(c, p),
           "id3_padding_nonzero", extract=lambda b: lab.id3.extract(b)),
    # An ISO-BMFF sample table says where the audio is; mdat bytes nothing
    # references are never read by anything.
    Vector("mp4-mdat", CORPUS_M4A, lambda c, p: lab.mp4.embed(c, p),
           "mp4_mdat_coverage", floor=1024,
           extract=lambda b: lab.mp4.extract(b)),
]


def _carrier(v):
    if not os.path.isfile(v.carrier):
        pytest.skip("no carrier for %s" % v.name)
    with open(v.carrier, "rb") as fh:
        return fh.read()


def _scan(tmp_path, blob, name):
    p = tmp_path / name
    p.write_bytes(blob)
    label, chunks, warns = walk_file(str(p))
    findings = anomalies.scan(str(p), label, chunks, warns) or []
    return ({f["rule"] for f in findings},
            cavity.account(str(p), label, chunks), warns)


@pytest.fixture(params=VECTORS, ids=lambda v: v.name)
def vector(request):
    return request.param


# ── the loop ────────────────────────────────────────────────────────

class TestTheLoop:
    def test_a_planted_payload_is_found(self, tmp_path, vector):
        carrier = _carrier(vector)
        rules, _rep, _w = _scan(tmp_path, vector.plant(carrier, PAYLOAD),
                                "planted" + os.path.splitext(vector.carrier)[1])
        assert vector.rule in rules, (
            "%s planted %d bytes and %s did not fire: %r"
            % (vector.name, len(PAYLOAD), vector.rule, sorted(rules)))

    def test_the_carrier_it_was_planted_in_is_clean(self, tmp_path, vector):
        """Without this the test above proves only that something is always
        reported. The same file, untouched, must come back with nothing."""
        carrier = _carrier(vector)
        rules, rep, warns = _scan(tmp_path, carrier,
                                  "clean" + os.path.splitext(vector.carrier)[1])
        assert vector.rule not in rules, (vector.name, sorted(rules))
        assert rep["regions"] == [], rep["regions"]
        assert not warns, warns

    def test_the_payload_survives_a_round_trip(self, vector):
        """Pins that the fixture really carries data. A planter that silently
        dropped the payload would satisfy every other assertion here."""
        if vector.extract is None:
            pytest.skip("%s has no extractor" % vector.name)
        assert vector.extract(vector.plant(_carrier(vector), PAYLOAD)) == PAYLOAD

    def test_the_file_stays_fully_accounted_for(self, tmp_path, vector):
        """The case that says coverage is necessary and not sufficient.

        Every byte is explained by a well-formed structure, nothing warns, and
        the payload is sitting right there. Geometry cannot answer this; only
        reading the content can.
        """
        carrier = _carrier(vector)
        _rules, rep, warns = _scan(tmp_path, vector.plant(carrier, PAYLOAD),
                                   "acct" + os.path.splitext(vector.carrier)[1])
        assert rep["coverage"] >= 0.9999, (vector.name, rep["coverage"])
        assert rep["regions"] == [], (vector.name, rep["regions"])
        assert not warns, (vector.name, warns)


# ── the size trades, pinned where they exist ────────────────────────

class TestFloors:
    """A floor is a measured trade against real-world noise. A trade that
    nothing pins is a number that drifts."""

    def test_a_payload_sized_run_is_reported(self, tmp_path, vector):
        carrier = _carrier(vector)
        big = (vector.floor or 256) * 2
        rules, _rep, _w = _scan(tmp_path, vector.plant(carrier, b"S" * big),
                                "big" + os.path.splitext(vector.carrier)[1])
        assert vector.rule in rules, (vector.name, big, sorted(rules))

    def test_below_the_floor_stays_quiet(self, tmp_path, vector):
        """Two of these have a floor and two do not, and the split is not
        arbitrary. RIFF JUNK and MP4 mdat both have a real population of
        innocent small runs -- DAW metadata in one, alignment and edit padding
        in the other -- so both trade a 1 KB floor for signal. FLAC PADDING and
        ID3 padding have no such population: their specs say zero and writers
        honour it, so any non-zero byte is worth saying.

        The MP4 floor was found BY this test rather than by reading. The table
        first claimed it had none, a 512-byte payload went unreported, and the
        rule turned out to say `gap > 1024` with "small gaps are legit
        alignment/edit padding" beside it. Asserting a trade is what surfaces
        the trade."""
        if vector.floor is None:
            pytest.skip("%s has no floor by design" % vector.name)
        carrier = _carrier(vector)
        rules, _rep, _w = _scan(tmp_path, vector.plant(carrier, b"S" * 512),
                                "small" + os.path.splitext(vector.carrier)[1])
        assert vector.rule not in rules, (
            "the %s floor has moved below the measured noise level"
            % vector.name)


# ── things that are true of one format only ─────────────────────────

class TestPerFormat:
    """Anything that genuinely cannot be a row in VECTORS. Kept small on
    purpose: the pull is to add a file per format, and four of those had
    already converged on the same six assertions."""

    def test_a_planted_wav_still_plays(self):
        """The reason a cavity is worth detecting at all: if planting broke the
        file, every reader would reject it and there would be nothing to hide
        in. WAV is the one carrier the standard library will open for us."""
        if not os.path.isfile(CORPUS_WAV):
            pytest.skip("no carrier WAV")
        with open(CORPUS_WAV, "rb") as fh:
            carrier = fh.read()
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with open(path, "wb") as fh:
                fh.write(lab.junk.embed(carrier, PAYLOAD))
            with wave.open(path) as w:
                assert w.getnframes() > 0 and w.getframerate() > 0
        finally:
            os.unlink(path)

    def test_a_junk_chunk_doing_its_job_is_not_a_finding(self, tmp_path):
        """Zero-filled JUNK is padding behaving exactly as specified. Built
        directly rather than through the planter, which stamps a marker that is
        itself non-uniform -- so planting zeroes does not produce padding."""
        if not os.path.isfile(CORPUS_WAV):
            pytest.skip("no carrier WAV")
        with open(CORPUS_WAV, "rb") as fh:
            carrier = fh.read()
        rules, _rep, _w = _scan(tmp_path, _raw_junk(carrier, b"\x00" * 2048),
                                "pad.wav")
        assert "cavity_content" not in rules, sorted(rules)

    def test_a_marker_less_payload_is_still_found(self, tmp_path):
        """Detection must not lean on the planter's own signature. `embed`
        stamps ACJK so `extract` can find its work again; nothing obliges an
        adversary to, and a rule keyed to it would catch only this repo."""
        if not os.path.isfile(CORPUS_WAV):
            pytest.skip("no carrier WAV")
        with open(CORPUS_WAV, "rb") as fh:
            carrier = fh.read()
        rules, _rep, _w = _scan(tmp_path, _raw_junk(carrier, b"S" * 2048),
                                "nomark.wav")
        assert "cavity_content" in rules, sorted(rules)

    def test_appended_bytes_are_unaccounted_not_hidden(self, tmp_path):
        """The crude cousin, and it must be told apart from the subtle ones:
        bytes after the container are outside every declaration, so geometry
        alone catches them and coverage drops below 1.0."""
        if not os.path.isfile(CORPUS_WAV):
            pytest.skip("no carrier WAV")
        with open(CORPUS_WAV, "rb") as fh:
            carrier = fh.read()
        rules, rep, warns = _scan(tmp_path, carrier + PAYLOAD, "tail.wav")
        hits = [r for r in rep["regions"] if r["kind"] == "unaccounted"]
        assert hits and hits[0]["length"] == len(PAYLOAD), rep["regions"]
        assert rep["coverage"] < 1.0
        assert "unaccounted_bytes" in rules
        assert warns, "appending past the declared size should already warn"

    def test_a_uniform_run_is_not_padding_by_itself(self, tmp_path):
        """4,096 repetitions of "S" appended to a WAV is perfectly uniform and
        perfectly obviously not padding. An earlier filter threw it away for
        being uniform; padding is a byte nobody chose for its content."""
        if not os.path.isfile(CORPUS_WAV):
            pytest.skip("no carrier WAV")
        with open(CORPUS_WAV, "rb") as fh:
            carrier = fh.read()
        _rules, rep, _w = _scan(tmp_path, carrier + b"S" * 4096, "uni.wav")
        hits = [r for r in rep["regions"] if r["kind"] == "unaccounted"]
        assert hits and hits[0]["length"] == 4096, rep["regions"]

    @pytest.mark.parametrize("fill", [b"\x00", b" ", b"\xff"])
    def test_real_padding_is_not_a_region(self, tmp_path, fill):
        if not os.path.isfile(CORPUS_WAV):
            pytest.skip("no carrier WAV")
        with open(CORPUS_WAV, "rb") as fh:
            carrier = fh.read()
        _rules, rep, _w = _scan(tmp_path, carrier + fill * 4096, "fill.wav")
        assert rep["regions"] == [], (fill, rep["regions"])


def _raw_junk(carrier, content):
    """A JUNK chunk carrying exactly `content`, with no planter marker."""
    body = content + (b"\x00" if len(content) % 2 else b"")
    chunk = b"JUNK" + struct.pack("<I", len(content)) + body
    out = bytearray(carrier)
    out[12:12] = chunk
    struct.pack_into("<I", out, 4, struct.unpack_from("<I", out, 4)[0] + len(chunk))
    return bytes(out)
