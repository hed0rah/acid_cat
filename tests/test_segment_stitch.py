"""A boundary we introduced is not a fact about the file.

Scanning a large image in fixed segments means every segment is analysed blind
to its neighbours, so a stream crossing an edge is seen twice: once as something
that ends at the edge, once as something that starts past it. Measured on a
187 MB archive holding 64 songs, scanned in 16 MB segments:

    regions reported            75      (the archive's own index says 64)
    bytes covered      187,729,570      (the index totals 187,777,310)

All eleven boundaries split a song, and the partial Ogg page straddling each one
was dropped entirely -- 120,045 bytes of audio that no region claimed. Eleven of
the seventy-five were unplayable second halves.

This is the same shape as the bug that started this work: regions whose offsets
sat on 16 MB boundaries were scan segments wearing a song's clothes. That fix
corrected page-versus-stream grouping WITHIN a segment. Nothing had ever looked
at what happens ACROSS one.

Rejoining is on identity, not proximity. Ogg pages carry a bitstream serial and
the sweep already records it; all eleven pairs shared one. Adjacency would have
been the wrong rule twice over -- the songs in that archive are packed back to
back, so merging neighbours collapsed the 64 into 27 runs.
"""

import pytest

from acidcat.core.forensics import locate as locatemod

SEG = 16 * 1024 * 1024


def _r(off, end, *, fmt="ogg", serials=(7,), kind="container"):
    return {"kind": kind, "format": fmt, "offset": off, "end": end,
            "length": end - off, "stream_serials": list(serials),
            "confidence": 0.9, "evidence": None}


class TestItRejoinsWhatTheScanCut:
    def test_a_stream_split_at_a_boundary_becomes_one_again(self):
        a = _r(SEG - 900_000, SEG - 990)
        b = _r(SEG + 3_182, SEG + 2_000_000)
        out = locatemod.stitch_segments([a, b], SEG)
        assert len(out) == 1
        assert out[0]["offset"] == SEG - 900_000
        assert out[0]["end"] == SEG + 2_000_000
        assert out[0]["length"] == out[0]["end"] - out[0]["offset"]

    def test_the_bytes_in_the_gap_are_reclaimed(self):
        """The partial page at the edge belonged to the stream. Leaving it out
        is not a smaller answer, it is a wrong one."""
        a, b = _r(0, SEG - 500), _r(SEG + 700, SEG * 2)
        out = locatemod.stitch_segments([a, b], SEG)
        assert out[0]["length"] == SEG * 2, "the gap was not reclaimed"

    def test_it_says_why_the_region_spans_a_boundary(self):
        """A region that was modified should be able to explain itself. The
        `evidence` key exists but holds None on a fresh record, so a naive
        setdefault-and-append silently attaches nothing."""
        out = locatemod.stitch_segments([_r(0, SEG - 500), _r(SEG + 700, SEG * 2)],
                                        SEG)
        assert any("rejoined" in str(e) for e in out[0]["evidence"])

    def test_a_stream_crossing_several_boundaries_is_rejoined_once(self):
        parts = [_r(0, SEG - 300), _r(SEG + 400, SEG * 2 - 300),
                 _r(SEG * 2 + 400, SEG * 3)]
        out = locatemod.stitch_segments(parts, SEG)
        assert len(out) == 1 and out[0]["end"] == SEG * 3

    def test_the_serials_of_both_halves_are_kept(self):
        a, b = _r(0, SEG - 500, serials=(7,)), _r(SEG + 700, SEG * 2, serials=(7, 9))
        out = locatemod.stitch_segments([a, b], SEG)
        assert out[0]["stream_serials"] == [7, 9]


class TestItRefusesWhereItCannotProveSameness:
    def test_two_regions_with_different_serials_stay_apart(self):
        """Different bitstreams that happen to meet at an edge are two files,
        and this is exactly where they meet."""
        a, b = _r(0, SEG - 500, serials=(7,)), _r(SEG + 700, SEG * 2, serials=(8,))
        assert len(locatemod.stitch_segments([a, b], SEG)) == 2

    def test_regions_with_no_serial_are_left_alone(self):
        """Nothing to prove sameness with, so nothing is claimed."""
        a, b = _r(0, SEG - 500, serials=()), _r(SEG + 700, SEG * 2, serials=())
        assert len(locatemod.stitch_segments([a, b], SEG)) == 2

    def test_adjacency_in_the_middle_of_a_segment_is_not_a_split(self):
        """The songs in a packed archive sit back to back. Merging on
        adjacency alone collapsed 64 of them into 27."""
        a, b = _r(1_000, 500_000), _r(500_000, 900_000)
        assert len(locatemod.stitch_segments([a, b], SEG)) == 2

    def test_a_gap_far_larger_than_a_page_is_not_bridged(self):
        a, b = _r(0, SEG - 500), _r(SEG + 5_000_000, SEG + 9_000_000)
        assert len(locatemod.stitch_segments([a, b], SEG)) == 2

    def test_different_formats_are_never_joined(self):
        a = _r(0, SEG - 500, fmt="ogg")
        b = _r(SEG + 700, SEG * 2, fmt="flac")
        assert len(locatemod.stitch_segments([a, b], SEG)) == 2

    def test_an_overlapping_pair_is_not_treated_as_a_split(self):
        a, b = _r(0, SEG + 1000), _r(SEG - 1000, SEG * 2)
        out = locatemod.stitch_segments([a, b], SEG)
        assert len(out) == 2


class TestItIsSafeOnTheOrdinaryCase:
    @pytest.mark.parametrize("regions", [[], [_r(0, 1000)]])
    def test_nothing_to_do_is_not_an_error(self, regions):
        assert len(locatemod.stitch_segments(list(regions), SEG)) == len(regions)

    def test_a_zero_segment_size_is_declined_rather_than_dividing_by_it(self):
        rs = [_r(0, 100), _r(200, 300)]
        assert locatemod.stitch_segments(rs, 0) == rs

    def test_a_scan_that_never_segmented_is_unchanged(self):
        """A whole-buffer scan has no boundaries of ours in it, so nothing
        here should ever fire."""
        rs = [_r(0, 1_000_000), _r(1_000_000, 2_000_000),
              _r(3_000_000, 4_000_000)]
        assert len(locatemod.stitch_segments(list(rs), SEG)) == 3


class TestTheBoundaryItselfIsIncluded:
    """`a_end` is exclusive, so a stream whose last complete page ends exactly
    ON a segment boundary has a_end == the boundary. Floor division put that in
    the NEXT segment, the two quotients matched, and the pair read as two
    regions that merely sit near each other -- the one case this whole function
    exists for was the one case it refused.

    Rare (roughly page-size over segment-size per boundary) and exactly the
    boundary-we-drew class of defect, so it fails silently as a split song.
    """

    def test_a_stream_ending_on_the_boundary_is_rejoined(self):
        a, b = _r(SEG - 900_000, SEG), _r(SEG + 3_000, SEG + 2_000_000)
        out = locatemod.stitch_segments([a, b], SEG)
        assert len(out) == 1, "a page landing flush on the boundary split a song"
        assert out[0]["end"] == SEG + 2_000_000

    def test_a_region_ending_up_to_the_boundary_is_a_split(self):
        """Up to AND including it. Not past it: a region ending at SEG+1 was
        produced BY the next segment's scan, so no boundary of ours ever cut
        it -- that is two regions inside one segment, which is the case the
        predicate is right to refuse. The first version of this test asserted
        otherwise and was wrong about which side the seam is on."""
        for a_end in (SEG - 1, SEG):
            pair = [_r(SEG - 500_000, a_end), _r(SEG + 4_000, SEG * 2)]
            assert len(locatemod.stitch_segments(pair, SEG)) == 1, a_end
        past = [_r(SEG + 1, SEG + 2_000), _r(SEG + 4_000, SEG * 2)]
        assert len(locatemod.stitch_segments(past, SEG)) == 2, (
            "two regions inside one segment were joined")

    def test_it_did_not_become_a_join_everything_rule(self):
        """The predicate got looser; it must not have got loose enough to
        merge two regions sitting in the middle of one segment."""
        pair = [_r(1_000, 500_000), _r(501_000, 900_000)]
        assert len(locatemod.stitch_segments(pair, SEG)) == 2
