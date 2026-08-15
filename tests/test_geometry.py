"""The one reader for "which bytes does this chunk occupy?".

Two ranges, not one, because one key was being asked two questions: RIFF's
`size` is the payload length and MP4's is the whole box including its header.
Under a single reader those disagree silently, and the disagreement showed up
as a WAV `data` node whose hex view began on the four ASCII bytes `data` and
ended eight bytes short of the audio.

The normalizer answers both questions separately and, just as importantly,
records which of the two answers anyone actually gave it.
"""

import pytest

from acidcat.core.infra import geometry as G


def _riff_like(off=0x24, size=2000):
    """A walker that states only `size`, meaning the payload. The default rule
    has to supply the eight-byte header."""
    return {"id": "data", "offset": off, "size": size}


def _mp4_like(off=0x100, box=3567, hdr=8):
    """A walker that states its own geometry: box size counts the header."""
    return {"id": "moov", "offset": off, "size": box,
            "payload_base": off + hdr, "payload_len": box - hdr,
            "extent_len": box}


class TestTheDefaultRule:
    def test_a_bare_chunk_gets_the_documented_header(self):
        c = _riff_like()
        G.normalize([c], filesize=1 << 20)
        assert c["payload_base"] == 0x24 + 8
        assert c["payload_len"] == 2000
        assert c["extent_len"] == 8 + 2000
        assert c["geometry"] == G.DEFAULTED

    def test_the_payload_is_where_the_contents_actually_are(self):
        """The bug this exists to stop: a range starting on the tag and ending
        eight bytes early."""
        c = _riff_like()
        G.normalize([c], filesize=1 << 20)
        base, n = G.payload_of(c)
        assert (base, base + n) == (0x2c, 0x2c + 2000)
        off, ext = G.extent_of(c)
        assert (off, off + ext) == (0x24, 0x2c + 2000)

    def test_it_does_not_touch_size(self):
        """Every existing consumer still reads `size` and must keep seeing
        exactly what its walker said."""
        c = _riff_like()
        G.normalize([c], filesize=1 << 20)
        assert c["size"] == 2000


class TestADeclaredGeometryIsBelieved:
    def test_a_box_size_that_counts_its_header_is_not_re_derived(self):
        c = _mp4_like()
        G.normalize([c], filesize=1 << 20)
        assert c["payload_len"] == 3567 - 8
        assert c["extent_len"] == 3567
        assert c["geometry"] == G.DECLARED

    def test_the_extent_ends_where_the_box_ends(self):
        c = _mp4_like(off=0x100, box=3567)
        G.normalize([c], filesize=1 << 20)
        off, ext = G.extent_of(c)
        assert off + ext == 0x100 + 3567, "the box overshot or undershot itself"

    def test_declared_and_defaulted_are_distinguishable(self):
        a, b = _mp4_like(), _riff_like()
        G.normalize([a, b], filesize=1 << 20)
        assert G.is_trustworthy(a) and not G.is_trustworthy(b)


class TestItRefusesToRepairWhatItCannotVerify:
    def test_a_payload_past_the_file_is_marked_not_fixed(self):
        """A geometry that was guessed and happens to fit is still a guess. The
        normalizer's job is to say it does not fit, not to hunt for a reading
        that does."""
        c = _riff_like(off=0, size=10_000)
        G.normalize([c], filesize=500)
        assert c["geometry"] == G.INVALID
        assert c["payload_len"] == 10_000, "it silently rewrote the walker"

    def test_an_extent_past_the_file_is_invalid_even_when_the_payload_fits(self):
        c = {"id": "x", "offset": 0, "size": 100,
             "payload_base": 0, "payload_len": 100, "extent_len": 10_000}
        G.normalize([c], filesize=500)
        assert c["geometry"] == G.INVALID

    def test_a_payload_base_before_its_chunk_is_invalid(self):
        c = {"id": "x", "offset": 100, "size": 10, "payload_base": 40}
        G.normalize([c], filesize=1 << 20)
        assert c["geometry"] == G.INVALID

    def test_a_chunk_with_no_position_is_said_to_have_none(self):
        """Derived chunks are real and are not ranges. Inventing 0..0 for them
        would put them in the hex pane at the start of the file."""
        c = {"id": "tags", "summary": "derived"}
        G.normalize([c], filesize=1 << 20)
        assert c["geometry"] == G.UNPOSITIONED
        assert "payload_base" not in c


class TestItWorksOnASlice:
    def test_a_walk_of_a_carved_region_is_checked_against_that_region(self):
        """Recursion walks a slice of a bigger file. "Fits in the file" then
        means "fits in the thing it was carved from", or every nested walk
        validates against a bound it cannot exceed and nothing is ever caught."""
        inside = {"id": "ok", "offset": 1000, "size": 50}
        outside = {"id": "no", "offset": 1000, "size": 5000}
        G.normalize([inside, outside], filesize=1 << 20,
                    parent_extent=(1000, 500))
        assert inside["geometry"] == G.DEFAULTED
        assert outside["geometry"] == G.INVALID


class TestTheAccessorsStandAlone:
    def test_they_read_an_unnormalized_chunk_by_the_documented_rule(self):
        """So every consumer can adopt them before every producer emits the
        keys -- which is the only way this migrates without a flag day."""
        raw = {"id": "fmt ", "offset": 0x0c, "size": 16}
        assert G.payload_of(raw) == (0x14, 16)
        assert G.extent_of(raw) == (0x0c, 24)

    def test_they_prefer_what_the_walker_said(self):
        assert G.payload_of(_mp4_like()) == (0x108, 3559)
        assert G.extent_of(_mp4_like()) == (0x100, 3567)

    @pytest.mark.parametrize("chunk", [
        {}, {"offset": None}, {"offset": 5}, {"offset": 5, "size": None},
    ])
    def test_they_do_not_raise_on_a_half_built_chunk(self, chunk):
        G.payload_of(chunk)
        G.extent_of(chunk)
