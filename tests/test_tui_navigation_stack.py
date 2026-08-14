"""Descending into a region is navigation, and navigation goes back.

The TUI could open a blob, locate regions in it, and descend into one. It could
not come back. `_region_view` was a single tuple and `_regions`/`_blob_src` were
single slots, so the model was exactly one blob and exactly one region:

  - a region inside a region could not be represented at all
  - pressing `l` inside a descended region repointed `_blob_src` at the carved
    temp and overwrote `_regions`, destroying the way back with no warning
  - `u` re-showed the region list as a modal over whatever was loaded rather
    than restoring the parent view, so the parent's tree, cursor and edits were
    gone
  - every descend leaked a temp file that lived until the app quit
  - `_scan_partial` was set when a scan was stopped early and never restored, so
    an ascended view of a partial scan looked complete

A frame stack fixes all five, because each of them is the same missing thing:
state that belongs to a view was being kept in one global slot.
"""

import asyncio
import os

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI      # noqa: E402


def _run(scenario):
    asyncio.run(scenario())


def _page(serial, seq, *, bos=False, eos=False, body=b"\x11" * 2000):
    import struct
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
    """An unknown container holding three Ogg streams -- the shape this whole
    feature exists for. Over 4 KiB so the TUI auto-offers the region browser."""
    p = tmp_path / "nested.blob"
    p.write_bytes(b"HDR!" + b"\x00" * 4000
                  + _stream(101) + _stream(202) + _stream(303))
    return str(p)


async def _ready(app, pilot):
    """Scan, the way a user now does: by expanding the file.

    Opening no longer starts a locate scan on its own -- on a 187 MB archive
    that was minutes of grinding before the UI answered, for a scan nobody
    asked for. Expanding the root is the ask.
    """
    app.query_one("#tree").root.expand()
    await pilot.pause(0.2)
    for _ in range(80):
        if app._regions is not None and not app._scanning:
            break
        await pilot.pause(0.1)
    while len(app.screen_stack) > 1:
        app.screen_stack[-1].dismiss(None)
        await pilot.pause()


class TestBackAndForward:
    def test_descend_then_back_restores_the_parent(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                assert len(app._regions) == 3
                top_src, top_size = app.src, app.fsize

                app._descend(0)
                await pilot.pause()
                assert app.src != top_src and app.fsize < top_size

                app.action_nav_back()
                await pilot.pause()
                assert app.src == top_src
                assert app.fsize == top_size
        _run(scenario)

    def test_back_does_not_rescan(self, blob):
        """The complaint that started this: Esc out and the results were gone,
        so seeing them again meant scanning the whole file again."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                before = app._regions
                app._descend(1)
                await pilot.pause()
                app.action_nav_back()
                await pilot.pause()
                assert app._regions is before, "the parent's regions were re-scanned"
                assert not app._scanning
        _run(scenario)

    def test_forward_returns_to_where_back_came_from(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                app._descend(0)
                await pilot.pause()
                inside = app.src
                app.action_nav_back()
                await pilot.pause()
                app.action_nav_forward()
                await pilot.pause()
                assert app.src == inside
        _run(scenario)

    def test_a_new_branch_abandons_the_forward_history(self, blob):
        """Browser semantics: descending somewhere else discards the trail you
        backed out of, and the temps that trail owned go with it."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                app._descend(0)
                await pilot.pause()
                abandoned = app.src
                app.action_nav_back()
                await pilot.pause()
                assert len(app._forward) == 1
                app._descend(2)
                await pilot.pause()
                assert app._forward == []
                assert not os.path.isfile(abandoned), "abandoned temp not freed"
        _run(scenario)

    def test_back_at_the_top_declines_instead_of_doing_nothing(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.action_nav_back()
                await pilot.pause()
                assert any("nothing to go back to" in n for n in notes), notes
        _run(scenario)


class TestNesting:
    def test_a_region_inside_a_region(self, blob):
        """Impossible before: the single `_region_view` slot could hold one
        level, and descending again overwrote the way home."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                app._descend(0)
                await pilot.pause()
                first = app.src

                app._regions = [{"kind": "carve", "format": None,
                                 "offset": 100, "end": 900, "length": 800}]
                app._blob_src = app.src
                app._descend(0)
                await pilot.pause()
                assert len(app._stack) == 2
                assert app.src != first

                app.action_nav_back()
                await pilot.pause()
                assert app.src == first
                app.action_nav_back()
                await pilot.pause()
                assert len(app._stack) == 0
        _run(scenario)

    def test_the_breadcrumb_shows_every_level(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                app._descend(0)
                await pilot.pause()
                app._regions = [{"kind": "carve", "format": None,
                                 "offset": 10, "end": 200, "length": 190}]
                app._blob_src = app.src
                app._descend(0)
                await pilot.pause()
                trail = app._display_name()
                assert trail.count(">") == 2, trail
                assert "nested.blob" in trail
        _run(scenario)


class TestTheSharpEdge:
    def test_locate_inside_a_region_keeps_the_parent_reachable(self, blob):
        """`l` used to repoint the blob source at the carved temp and overwrite
        the region list, so the parent became unreachable and nothing said so.

        Each frame owning its own regions is what makes this safe.
        """
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                parent_src = app.src
                parent_regions = app._regions

                app._descend(0)
                await pilot.pause()
                # scan inside the carved region, the destructive move
                app.action_locate_regions()
                for _ in range(80):
                    if not app._scanning:
                        break
                    await pilot.pause(0.1)
                while len(app.screen_stack) > 1:
                    app.screen_stack[-1].dismiss(None)
                    await pilot.pause()

                app.action_nav_back()
                await pilot.pause()
                assert app.src == parent_src, "the way back was destroyed"
                assert app._regions is parent_regions
        _run(scenario)


class TestFrameOwnedState:
    def test_the_partial_scan_flag_travels_with_the_view(self, blob):
        """`_scan_partial` marks a scan the user stopped early. It was global,
        so ascending showed a partial list as though it were complete."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                app._scan_partial = True
                app._descend(0)
                await pilot.pause()
                assert app._scan_partial is False, "inherited the parent's flag"
                app.action_nav_back()
                await pilot.pause()
                assert app._scan_partial is True, "the parent's flag was lost"
        _run(scenario)

    def test_the_cursor_comes_back_where_it_was(self, blob):
        """Measured on a descended Ogg, because the blob itself walks as
        `unsupported` and its tree is a single root node -- there is nowhere for
        a cursor to be, so testing it there proves nothing."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                app._descend(0)                      # an Ogg, with a real tree
                await pilot.pause()
                tree = app.query_one("#tree")
                assert tree.last_line > 0, "fixture gave no tree to navigate"
                for _ in range(3):
                    await pilot.press("down")
                await pilot.pause()
                where = tree.cursor_line
                assert where > 0

                app._regions = [{"kind": "carve", "format": None,
                                 "offset": 10, "end": 400, "length": 390}]
                app._blob_src = app.src
                app._descend(0)
                await pilot.pause()
                app.action_nav_back()
                await pilot.pause()
                assert app.query_one("#tree").cursor_line == where
        _run(scenario)

    def test_temps_are_freed_when_the_file_is_closed(self, blob, tmp_path):
        """One temp per descend used to survive until the app quit."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await _ready(app, pilot)
                app._descend(0)
                await pilot.pause()
                carved = app.src
                assert os.path.isfile(carved)

                other = tmp_path / "other.bin"
                other.write_bytes(b"\x00" * 5000)
                app._open_path(str(other))
                await pilot.pause()
                assert not os.path.isfile(carved), "descend temp outlived its frame"
                assert app._stack == [] and app._forward == []
        _run(scenario)
