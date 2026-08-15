"""Located regions belong in the tree, because that is what they are.

They used to live behind a modal: a second grammar for the file's contents, in
a UI that already had one. The tree is the file, and a region found inside it is
a child of the file -- so as tree nodes they get the hex pane, the graphs, `p`
and the cursor for free, since a node with a byte range is all any of those ever
needed.

Two consequences follow:

  Opening no longer scans. A 187 MB archive ground for minutes before the UI
  answered, for a scan nobody had asked for. The root is expandable instead, and
  expanding it is the ask -- which is what expanding a node means everywhere
  else in this tree.

  The list stops being how you navigate and becomes how you act in bulk. That is
  the one thing a tree cannot express: selecting some regions and extracting
  exactly those.
"""

import asyncio
import struct

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI          # noqa: E402
from acidcat.tui_app.screens import RegionsScreen   # noqa: E402


def _run(scenario):
    asyncio.run(scenario())


def _page(serial, seq, *, bos=False, eos=False, body=b"\x11" * 2000):
    htype = (0x02 if bos else 0) | (0x04 if eos else 0)
    segs, rest = [], len(body)
    while rest >= 255:
        segs.append(255)
        rest -= 255
    segs.append(rest)
    return (b"OggS" + bytes([0, htype]) + struct.pack("<q", seq * 1000)
            + struct.pack("<I", serial) + struct.pack("<I", seq)
            + struct.pack("<I", 0) + bytes([len(segs)]) + bytes(segs) + body)


def _stream(serial, pages=12):
    return b"".join(_page(serial, i, bos=(i == 0), eos=(i == pages - 1))
                    for i in range(pages))


@pytest.fixture
def blob(tmp_path):
    p = tmp_path / "archive.blob"
    p.write_bytes(b"HDR!" + b"\x00" * 4000
                  + _stream(101) + _stream(202) + _stream(303))
    return str(p)


async def _scan(app, pilot, want_list=False):
    if want_list:
        app.action_locate_regions()
    else:
        app.query_one("#tree").root.expand()
    await pilot.pause(0.2)
    for _ in range(80):
        if app._regions is not None and not app._scanning:
            break
        await pilot.pause(0.1)


class TestOpeningDoesNotScan:
    def test_no_scan_starts_on_its_own(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                assert app._regions is None
                assert not app._scanning, "started a scan nobody asked for"
        _run(scenario)

    def test_the_root_offers_itself_for_expansion(self, blob):
        """Otherwise there is nothing on screen to suggest the file has
        contents, and no obvious way to ask."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                assert app.query_one("#tree").root.allow_expand is True
        _run(scenario)

    def test_expanding_the_root_runs_the_scan(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                await _scan(app, pilot)
                assert app._regions and len(app._regions) == 3
        _run(scenario)

    def test_a_walked_file_is_not_offered_a_scan(self, tmp_path):
        """`_scannable` is about containers no walker claims. A WAV has real
        chunks and expanding it must show those, not start a locate sweep."""
        n = 2000
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
                + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
                + b"data" + struct.pack("<I", n) + b"\x00" * n)
        p = tmp_path / "real.wav"
        p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

        async def scenario():
            app = AcidcatTUI(str(p))
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                assert app._scannable() is False
                assert not app._scanning
        _run(scenario)


class TestAScanAnnouncesItself:
    """A scan can run for minutes on a large file. Starting one without saying
    so leaves the key that started it looking broken for exactly that long.

    The announcement lived at one of the two callers, so expanding the file
    said it and `l` did not. It belongs to the scan, not to a way of asking
    for one.
    """

    def test_the_key_says_it_started_one(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.action_locate_regions()
                await pilot.pause(0.2)
                assert any("scanning" in n for n in notes), notes
        _run(scenario)

    def test_expanding_the_file_says_it_too(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.query_one("#tree").root.expand()
                await pilot.pause(0.3)
                said = [n for n in notes if "scanning" in n]
                assert said, notes
                assert len(said) == 1, f"announced twice: {said}"
        _run(scenario)

    def test_a_cached_list_does_not_claim_to_be_scanning(self, blob):
        """`l` after a scan is instant. Saying "scanning" over an instant
        answer is the same lie in the other direction."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                while len(app.screen_stack) > 1:
                    app.screen_stack[-1].dismiss(None)
                    await pilot.pause()
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.action_locate_regions()
                await pilot.pause(0.3)
                assert not [n for n in notes if "scanning" in n], notes
        _run(scenario)


class TestRegionsAreTreeNodes:
    def _region_nodes(self, app):
        return [c for c in app.query_one("#tree").root.children
                if (app._info(c) is not None and app._info(c).region is not None)]

    def test_the_scan_puts_them_under_the_file(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                assert len(self._region_nodes(app)) == 3
        _run(scenario)

    def test_the_scan_does_not_open_the_list(self, blob):
        """Expanding asked for the tree to be filled, not for a modal."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                assert not [s for s in app.screen_stack
                            if isinstance(s, RegionsScreen)]
        _run(scenario)

    def test_l_still_opens_the_list_without_rescanning(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                cached = app._regions
                app.action_locate_regions()
                await pilot.pause(0.4)
                assert [s for s in app.screen_stack
                        if isinstance(s, RegionsScreen)]
                assert app._regions is cached, "l rescanned instead of reusing"
        _run(scenario)

    def test_a_region_node_carries_its_byte_range(self, blob):
        """This is what makes the hex pane, the graphs and `p` work on it with
        no special cases: it is an ordinary node."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                for node, r in zip(self._region_nodes(app), app._regions):
                    off, length, _accent = app._meta(node)
                    assert off == r["offset"]
                    assert length == r["length"]
        _run(scenario)

    def test_the_label_names_the_sniffed_format(self, blob):
        """Sniffing every region is what lets a node say `ogg` rather than
        `region`, and decides which are worth offering to expand."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                labels = [app._node_name(n) for n in self._region_nodes(app)]
                assert all("ogg" in x for x in labels), labels
        _run(scenario)


class TestExpandingARegionWalksIt:
    def test_it_hangs_the_chunks_under_the_region(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                node = [c for c in app.query_one("#tree").root.children
                        if (app._info(c) is not None and app._info(c).region is not None)][0]
                assert node.allow_expand is True
                assert not node.children, "walked before being asked"
                node.expand()
                await pilot.pause(0.5)
                assert node.children, "expanding did not walk the region"
        _run(scenario)

    def test_chunk_offsets_are_rebased_onto_the_parent(self, blob):
        """The walker sees a carved temp, so its offsets start at zero. Every
        other part of the UI reads the file that is open, so a child node
        carrying the temp's offsets would point the hex pane at the wrong
        bytes -- silently, since both are valid offsets."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                node = [c for c in app.query_one("#tree").root.children
                        if (app._info(c) is not None and app._info(c).region is not None)][0]
                base = app._regions[app._info(node).region]["offset"]
                assert base > 0, "fixture must not put region 0 at offset 0"
                node.expand()
                await pilot.pause(0.5)
                kids = [c for c in node.children if (app._meta(c) is not None)]
                assert kids
                for c in kids:
                    off, _len, _a = app._meta(c)
                    assert off >= base, (
                        f"child at 0x{off:08x} is below its region at "
                        f"0x{base:08x} -- offsets were not rebased")
        _run(scenario)

    def test_expanding_twice_does_not_double_the_children(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                node = [c for c in app.query_one("#tree").root.children
                        if (app._info(c) is not None and app._info(c).region is not None)][0]
                node.expand()
                await pilot.pause(0.5)
                first = len(node.children)
                node.collapse()
                node.expand()
                await pilot.pause(0.5)
                assert len(node.children) == first
        _run(scenario)


class TestTheTreeGoesAllTheWayDown:
    """The tree dead-ended two levels in: file > region > chunk, and a chunk
    would not open even when the walker had returned fields for it.

    The cause was one argument. `add_leaf` is `add(..., allow_expand=False)`,
    so the lazy region path had declared every chunk a leaf before asking
    whether it had anything under it -- and it never rendered the fields it was
    already being handed. Measured on this fixture the shallow walk returns 8
    fields across 2 chunks, all of them dropped.
    """

    def _chunks_of_a_region(self, app):
        node = [c for c in app.query_one("#tree").root.children
                if (app._info(c) is not None and app._info(c).region is not None)][0]
        node.expand()
        return node

    def test_a_chunk_with_fields_can_be_opened(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._chunks_of_a_region(app)
                await pilot.pause(0.5)
                openable = [c for c in region.children if c.allow_expand]
                assert openable, (
                    "level 3 unreachable: every chunk under the region was "
                    "declared a leaf")
        _run(scenario)

    def test_opening_it_shows_the_fields(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._chunks_of_a_region(app)
                await pilot.pause(0.5)
                chunk = [c for c in region.children if c.allow_expand][0]
                chunk.expand()
                await pilot.pause(0.2)
                names = [app._node_name(f) for f in chunk.children]
                assert names, "expandable, but nothing under it"
                assert any("=" in n for n in names), names
        _run(scenario)

    def test_the_fields_carry_a_byte_range_in_the_parent_space(self, blob):
        """A field node is only worth having if the hex pane can follow it, and
        that means the region's base, not the carved temp's zero."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._chunks_of_a_region(app)
                await pilot.pause(0.5)
                base = app._regions[app._info(region).region]["offset"]
                chunk = [c for c in region.children if c.allow_expand][0]
                chunk.expand()
                await pilot.pause(0.2)
                located = [app._meta(f) for f in chunk.children
                           if (app._meta(f) is not None)
                           and app._meta(f)[0] is not None]
                assert located, "no field node knew where it was"
                for off, _ln, _a in located:
                    assert off >= base, (
                        f"field at 0x{off:08x} sits below its region at "
                        f"0x{base:08x}")
        _run(scenario)

    def test_a_chunk_with_nothing_under_it_stays_shut(self, blob):
        """An arrow that opens onto nothing is a worse lie than no arrow."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._chunks_of_a_region(app)
                await pilot.pause(0.5)
                for c in region.children:
                    if not c.allow_expand:
                        continue
                    c.expand()
                    await pilot.pause(0.2)
                    assert c.children, (
                        f"{app._node_name(c)!r} claims children and has none")
        _run(scenario)


class TestTheHelpColumnsToo:
    """The same collision, found in the help screen while documenting the fix
    for it: the key column padded to 16 and `shift+left/right` is 16 wide.
    Nothing that pads without a separator is safe from its own widest entry.
    """

    def test_the_widest_key_is_not_glued_to_its_description(self, blob):
        from textual.widgets import Static

        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                app.action_help()
                await pilot.pause(0.3)
                text = app.screen_stack[-1].query_one(Static).content.plain
                assert "shift+left/right" in text, "the key is undocumented"
                line = [x for x in text.splitlines()
                        if "shift+left/right" in x][0]
                after = line.split("shift+left/right", 1)[1]
                assert after.startswith("  "), repr(line)
        _run(scenario)


class TestTheLabelColumns:
    """`comments` is exactly eight characters, and the id was padded to eight
    with no separator after it, so it rendered as `comments0x0000bb31`. The pad
    was doing the separator's job, which works right up until an id is as wide
    as the pad.
    """

    def test_an_id_that_fills_the_pad_still_has_a_gap_after_it(self):
        lbl = AcidcatTUI._chunk_label(
            AcidcatTUI, {"id": "comments", "size": 12}, 0xbb31, 8).plain
        assert "comments0x" not in lbl, lbl
        assert "comments  0x0000bb31" in lbl, lbl

    def test_the_column_widens_for_the_widest_id_in_the_group(self):
        chunks = [{"id": "OggS"}, {"id": "comments"}]
        assert AcidcatTUI._id_width(chunks) == 8
        starts = [AcidcatTUI._chunk_label(AcidcatTUI, c, 0, 8).plain.index("0x")
                  for c in chunks]
        assert len(set(starts)) == 1, "offsets did not line up"

    def test_a_very_long_id_is_capped_rather_than_shoving_the_row_across(self):
        wide = [{"id": "x" * 40}]
        assert AcidcatTUI._id_width(wide) == 12

    def test_a_shortened_id_says_it_was_shortened(self):
        """The column is capped, so a long id gets cut. An id printed whole
        when it is not whole names a chunk that is not in the file."""
        lbl = AcidcatTUI._chunk_label(
            AcidcatTUI, {"id": "SampleParameters", "size": 4}, 0, 12).plain
        assert "SampleParameters" not in lbl
        assert "…" in lbl, lbl

    def test_an_id_that_fits_is_left_alone(self):
        lbl = AcidcatTUI._chunk_label(
            AcidcatTUI, {"id": "fmt ", "size": 16}, 0, 6).plain
        assert "…" not in lbl
        assert "fmt" in lbl

    def test_each_chunk_keeps_its_own_colour(self):
        """The id is drawn in the accent that chunk's bytes are highlighted in,
        which is the only thing tying a tree row to a stripe in the hex pane.
        Sharing one builder must not flatten them to a single colour."""
        a = AcidcatTUI._chunk_label(AcidcatTUI, {"id": "fmt ", "size": 1}, 0, 6,
                                    False, "#ff0000")
        b = AcidcatTUI._chunk_label(AcidcatTUI, {"id": "fmt ", "size": 1}, 0, 6,
                                    False, "#00ff00")
        assert a.spans[1].style != b.spans[1].style

    def test_both_tree_builders_use_it(self):
        """There were two label builders, one padding to 6 and one to 8, and
        the collision was fixed in neither until they became one. Each of them
        has to go through the shared one, or the next fix lands in half the
        tree again."""
        import inspect as _i
        for fn in (AcidcatTUI._load, AcidcatTUI._expand_region_node):
            src = _i.getsource(fn)
            assert "self._chunk_label(" in src, fn.__name__
            assert "lbl.append(f\"{cid" not in src, (
                f"{fn.__name__} still formats an id column of its own")


class TestSelectingForExtraction:
    """The one job a tree cannot do: mark some regions and extract exactly
    those."""

    async def _list(self, app, pilot):
        app.action_locate_regions()
        await pilot.pause(0.2)
        for _ in range(80):
            if app._regions is not None and not app._scanning:
                break
            await pilot.pause(0.1)
        return self._screen(app)

    def _screen(self, app):
        # the LIVE one: toggling re-pushes, so the first in the stack is stale
        return [s for s in app.screen_stack if isinstance(s, RegionsScreen)][-1]

    def test_space_marks_and_unmarks(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                s = await self._list(app, pilot)
                assert app._region_sel == set()
                s.action_toggle_sel()
                await pilot.pause(0.3)
                assert app._region_sel == {0}
                self._screen(app).action_toggle_sel()
                await pilot.pause(0.3)
                assert app._region_sel == set()
        _run(scenario)

    def test_a_selects_all_then_none(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                s = await self._list(app, pilot)
                s.action_select_all()
                await pilot.pause(0.3)
                assert app._region_sel == {0, 1, 2}
                self._screen(app).action_select_all()
                await pilot.pause(0.3)
                assert app._region_sel == set()
        _run(scenario)

    def test_extract_takes_exactly_the_marked_ones(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await self._list(app, pilot)
                got = {}
                app._extract = lambda regs: got.__setitem__(
                    "offsets", [r["offset"] for r in regs])
                app._region_sel = {0, 2}
                app._show_regions(app._regions)
                await pilot.pause(0.3)
                self._screen(app).action_extract()
                await pilot.pause(0.3)
                assert got["offsets"] == [app._regions[0]["offset"],
                                          app._regions[2]["offset"]]
        _run(scenario)

    def test_extract_falls_back_to_the_cursor_when_nothing_is_marked(self, blob):
        """Selection must not become mandatory for the single-region case."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await self._list(app, pilot)
                got = {}
                app._extract = lambda regs: got.__setitem__("n", len(regs))
                self._screen(app).action_extract()
                await pilot.pause(0.3)
                assert got["n"] == 1
        _run(scenario)

    def test_extract_all_ignores_the_selection(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await self._list(app, pilot)
                got = {}
                app._extract = lambda regs: got.__setitem__("n", len(regs))
                app._region_sel = {1}
                app._show_regions(app._regions)
                await pilot.pause(0.3)
                self._screen(app).action_extract_all()
                await pilot.pause(0.3)
                assert got["n"] == 3
        _run(scenario)

    def test_the_selection_survives_the_list_being_rebuilt(self, blob):
        """It is re-pushed on every toggle and whenever a scan lands. A
        selection that did not survive that would be worse than none."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await self._list(app, pilot)
                app._region_sel = {1, 2}
                app._show_regions(app._regions)
                await pilot.pause(0.3)
                assert self._screen(app).selected == {1, 2}
        _run(scenario)

    def test_the_selection_belongs_to_the_view(self, blob):
        assert "_region_sel" in AcidcatTUI._FRAME_ATTRS
