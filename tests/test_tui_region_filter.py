"""Narrowing a region list, and the translation that makes it safe.

A table of contents turns an archive into hundreds of named rows -- 456 for a
shipped Duke Nukem .grp -- and a list that long is only usable if you can cut
it down. Typing does that.

The risk is not the filtering. It is that every action on this screen acts on a
REGION and reads a TABLE ROW to find it, and those are the same number only
while nothing is hidden. Hide one row above the cursor and descend, extract and
select all move to the wrong file, silently and plausibly. So most of what is
asserted here is the translation, not the narrowing.
"""

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.screens import RegionsScreen      # noqa: E402


def regions(names):
    """Shaped like what the table-of-contents path actually emits: a name for
    every entry, and a format only for the ones a walker claims. Giving every
    row the same format made a filter on it match everything, which passed
    three tests for the wrong reason."""
    out = []
    for i, n in enumerate(names):
        ext = n.rsplit(".", 1)[-1].lower()
        fmt = {"voc": "voc", "mid": "midi"}.get(ext)
        out.append({"kind": "container" if fmt else "entry", "format": fmt,
                    "offset": i * 0x100, "end": i * 0x100 + 0x80,
                    "length": 0x80, "confidence": 0.8, "name": n})
    return out


NAMES = ["CINEOV2.ANM", "DUKETEAM.ANM", "TILES000.ART", "WHISTLE.VOC",
         "WHIPYU01.VOC", "SQUISH.VOC", "GAME.CON", "E1L1.MAP",
         # a name that shares no substring with its format, so a query for the
         # format cannot be satisfied by the name column by accident
         "233C.MID"]


def screen(filter_text="", selected=None):
    s = RegionsScreen(regions(NAMES), "duke.grp", filter_text=filter_text,
                      selected=selected)
    s._view = s._visible()
    return s


class TestNarrowing:
    def test_no_filter_shows_everything(self):
        assert screen()._view == list(range(len(NAMES)))

    def test_a_substring_narrows_by_name(self):
        s = screen("whi")
        assert [NAMES[i] for i in s._view] == ["WHISTLE.VOC", "WHIPYU01.VOC"]

    def test_matching_is_case_insensitive(self):
        """The names are uppercase and nobody is going to type that."""
        assert screen("whistle")._view == screen("WHISTLE")._view
        assert len(screen("whistle")._view) == 1

    def test_it_also_matches_the_format(self):
        """The format column is searchable, and proving it needs a query the
        NAME cannot answer. `voc` is not that query: every VOC is called
        `*.VOC`, so a filter that only ever read names passed this test.
        `midi` is -- the file is called 233C.MID and the format is `midi`.
        """
        s = screen("midi")
        assert [NAMES[i] for i in s._view] == ["233C.MID"]
        assert "midi" not in "233C.MID".lower(), (
            "the point of this fixture is that the name does not contain it")

    def test_filtering_by_extension_still_works_through_the_name(self):
        s = screen("voc")
        assert [NAMES[i] for i in s._view] == ["WHISTLE.VOC", "WHIPYU01.VOC",
                                               "SQUISH.VOC"]

    def test_a_filter_matching_nothing_shows_nothing(self):
        assert screen("zzzz")._view == []


class TestTheTranslation:
    """The part that would corrupt an extraction rather than annoy someone."""

    def test_a_table_row_maps_to_its_real_region(self):
        s = screen("whi")
        assert s._index(0) == 3, "row 0 is WHISTLE.VOC, region 3"
        assert s._index(1) == 4, "row 1 is WHIPYU01.VOC, region 4"

    def test_without_a_filter_the_two_agree(self):
        s = screen()
        assert [s._index(i) for i in range(len(NAMES))] == list(range(len(NAMES)))

    def test_the_last_visible_row_is_not_the_last_region(self):
        """The failure this prevents, stated as the arithmetic that causes it:
        two visible rows over eight regions, so a reader that trusts the row
        number lands on region 1 when it means region 4."""
        s = screen("whi")
        assert len(s._view) == 2 and len(s.regions) == len(NAMES)
        assert s._index(1) != 1
        assert NAMES[s._index(1)] == "WHIPYU01.VOC"

    def test_an_out_of_range_row_does_not_reach_a_wrong_region(self):
        """A stale cursor after the filter shortens the list must not index
        into whatever now sits at that position."""
        s = screen("whi")
        assert s._index(99) in s._view
        assert s._index(-5) in s._view

    def test_an_empty_view_does_not_raise(self):
        s = screen("zzzz")
        assert s._index(0) == 0, "no rows, so nothing to act on; must not crash"


class TestSelectAllMeansWhatYouCanSee:
    def test_select_all_takes_the_visible_rows(self):
        """Type `voc`, press A, press X: the sounds come out and nothing else
        does. Selecting the whole archive from a filtered view would be a
        surprise, and the surprise would be measured in files written."""
        s = screen("voc")
        vis, sel = set(s._view), set()
        sel = (sel - vis) if vis and vis <= sel else (sel | vis)
        assert sel == {3, 4, 5}
        assert 0 not in sel, "an unfiltered-out region was selected"

    def test_selecting_again_clears_only_the_visible_ones(self):
        """A selection made under one filter must survive being toggled under
        another, or marking across two searches is impossible."""
        s = screen("voc", selected={0, 3, 4, 5})
        vis, sel = set(s._view), set(s.selected)
        sel = (sel - vis) if vis and vis <= sel else (sel | vis)
        assert sel == {0}, "clearing the VOCs also dropped a mark made earlier"


def sorted_screen(col="offset", desc=False, filter_text=""):
    s = RegionsScreen(regions(NAMES), "duke.grp", filter_text=filter_text,
                      sort_col=col, sort_desc=desc)
    s._sortable = [c for c in ["offset", "end", "kind", "format", "conf",
                               "length", "name"]]
    s._view = s._visible()
    return s


class TestSorting:
    def test_the_default_order_is_the_archive_order(self):
        """Offset ascending is how the file stores them, so "sorted by offset"
        and "not sorted" are the same list -- which makes getting back to it
        one keystroke rather than a special case."""
        s = sorted_screen()
        assert s._view == list(range(len(NAMES)))

    def test_sorting_by_name(self):
        s = sorted_screen("name")
        got = [NAMES[i] for i in s._view]
        assert got == sorted(NAMES, key=str.lower)

    def test_descending_reverses_it(self):
        s = sorted_screen("name", desc=True)
        got = [NAMES[i] for i in s._view]
        assert got == sorted(NAMES, key=str.lower, reverse=True)

    def test_sorting_is_stable(self):
        """Sorting by format leaves the VOCs in archive order, so the second
        key comes free. An unstable sort would reshuffle equal rows on every
        redraw, and this screen redraws on every keystroke."""
        s = sorted_screen("format")
        vocs = [NAMES[i] for i in s._view if NAMES[i].endswith(".VOC")]
        assert vocs == ["WHISTLE.VOC", "WHIPYU01.VOC", "SQUISH.VOC"], vocs

    def test_sorting_by_a_numeric_column_is_numeric(self):
        """`length` as text puts 1,000 before 999. The column has to sort by
        the value, not by how it is rendered."""
        rs = regions(NAMES)
        for i, n in enumerate([5, 100, 20, 3000, 7, 40, 900, 60, 8]):
            rs[i]["length"] = n
        s = RegionsScreen(rs, "duke.grp", sort_col="length")
        s._view = s._visible()
        assert [rs[i]["length"] for i in s._view] == sorted(
            r["length"] for r in rs)


class TestSortAndFilterCompose:
    def test_a_sort_applies_within_the_filter(self):
        s = sorted_screen("name", filter_text="voc")
        got = [NAMES[i] for i in s._view]
        assert got == ["SQUISH.VOC", "WHIPYU01.VOC", "WHISTLE.VOC"]

    def test_the_translation_survives_being_reordered(self):
        """The thing that would corrupt an extraction. Sorting reorders the
        view, so row 0 is whatever now sits first -- and descend and extract
        both read a row and mean a region."""
        s = sorted_screen("name", desc=True)
        first = [NAMES[i] for i in s._view][0]
        assert NAMES[s._index(0)] == first
        assert s._index(0) != 0, (
            "reversed, row 0 must not still be region 0, or this proves nothing")

    def test_every_visible_row_maps_to_its_own_region(self):
        """Swept rather than spot-checked: any permutation that lost or
        duplicated an index would extract one file twice and another never."""
        for col in ("offset", "name", "length", "format"):
            for desc in (False, True):
                s = sorted_screen(col, desc)
                idx = [s._index(r) for r in range(len(s._view))]
                assert len(set(idx)) == len(idx), (col, desc, "duplicate")
                assert set(idx) == set(range(len(NAMES))), (col, desc, "lost one")


def test_a_column_with_no_sort_key_leaves_the_order_alone():
    """Not hypothetical. The last column is `name` only when the regions have
    names -- a plain locate sweep shows `geometry` there instead, and the
    optional `shape` column has no ordering either. Carrying a sort across
    that change must leave the list in archive order rather than reversing it
    or raising."""
    s = RegionsScreen(regions(NAMES), "duke.grp", sort_col="geometry")
    s._view = s._visible()
    assert s._view == list(range(len(NAMES)))
    s2 = RegionsScreen(regions(NAMES), "duke.grp", sort_col="shape",
                       sort_desc=True)
    s2._view = s2._visible()
    assert s2._view == list(range(len(NAMES))), (
        "an unsortable column must not silently reverse the list")
