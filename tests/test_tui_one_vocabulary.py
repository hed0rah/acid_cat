"""One set of keys, spelled the same wherever you are.

Six single letters used to mean two different things depending on which screen
was in front of you:

    a   expand all      / select all-or-none
    c   collapse all    / manual carve
    e   edit field      / extract all
    m   byte map        / cycle mode
    s   strip metadata  / shape column
    x   follow pointer  / extract selected

So moving between the tree and the region list meant relearning the tool, and
the one thing a person most wants to do -- mark some regions and extract those --
existed on exactly one of the two screens with nothing saying so. `space` marked
a row in the list and paused a scan in the tree.

The rule: lowercase looks, SHIFT acts on the selection. `space` marks, `A` marks
all, `X` extracts what is marked, `E` extracts everything -- identical in both
places. The keys that remain list-only (mode, lens, shape, carve) have no twin
in the tree, so they collide with nothing.
"""

import asyncio
import re
import struct
from pathlib import Path

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI          # noqa: E402
from acidcat.tui_app.screens import RegionsScreen   # noqa: E402
from conftest import press_until, until             # noqa: E402


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
    p = tmp_path / "three.blob"
    p.write_bytes(b"HDR!" + bytes(4000)
                  + _stream(101) + _stream(202) + _stream(303))
    return str(p)


async def _scanned(app, pilot):
    app.query_one("#tree").root.expand()
    await pilot.pause(0.2)
    for _ in range(80):
        if app._regions is not None and not app._scanning:
            break
        await pilot.pause(0.1)
    while len(app.screen_stack) > 1:
        app.screen_stack[-1].dismiss(None)
        await pilot.pause()
    return [c for c in app.query_one("#tree").root.children
            if app._info(c) is not None and app._info(c).region is not None]


class TestTheTwoScreensAgree:
    def test_no_letter_means_two_things(self):
        """The measurement that started this: six collisions."""
        main = {}
        for b in AcidcatTUI.BINDINGS:
            key = b[0] if isinstance(b, tuple) else b.key
            desc = (b[2] if isinstance(b, tuple) and len(b) > 2
                    else getattr(b, "description", "")) or ""
            if len(key) == 1 and key.isalpha():
                main[key] = desc
        src = Path("src/acidcat/tui_app/screens.py").read_text(encoding="utf-8")
        block = src[src.index("class RegionsScreen"):]
        block = block[:block.index("def compose")]
        listed = dict(re.findall(r'\("([a-zA-Z])",\s*"[a-z_]+",\s*"([^"]+)"\)',
                                 block))
        clashes = {k for k in set(main) & set(listed) if main[k] != listed[k]}
        assert not clashes, (
            "these letters mean different things on the two screens: "
            + ", ".join(f"{k} ({main[k]!r} vs {listed[k]!r})"
                        for k in sorted(clashes)))

    @pytest.mark.parametrize("key", ["space", "A", "X", "E"])
    def test_the_action_keys_exist_in_both(self, key):
        main = {b[0] if isinstance(b, tuple) else b.key
                for b in AcidcatTUI.BINDINGS}
        listed = {b[0] for b in RegionsScreen.BINDINGS}
        assert key in main, f"{key} missing from the tree"
        assert key in listed, f"{key} missing from the region list"


class TestSelectingFromTheTree:
    def test_space_marks_the_region_under_the_cursor(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                regions = await _scanned(app, pilot)
                app._cur_node = regions[1]
                app.action_select_region()
                assert app._region_sel == {1}
                app.action_select_region()
                assert app._region_sel == set(), "it does not unmark"
        _run(scenario)

    def test_the_mark_is_visible_on_the_row(self, blob):
        """A selection you cannot see is a selection you cannot trust."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                regions = await _scanned(app, pilot)
                app._cur_node = regions[0]
                app.action_select_region()
                assert "[x]" in app._node_name(regions[0])
                assert "[ ]" in app._node_name(regions[1])
        _run(scenario)

    def test_marking_a_chunk_marks_the_region_it_lives_in(self, blob):
        """Otherwise selection works on one row of the tree and looks broken on
        every other, which is worse than not having it."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                regions = await _scanned(app, pilot)
                regions[2].expand()
                await pilot.pause(0.5)
                assert regions[2].children, "nothing under the region to select from"
                app._cur_node = regions[2].children[0]
                app.action_select_region()
                assert app._region_sel == {2}
        _run(scenario)

    def test_x_extracts_exactly_what_is_marked(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                regions = await _scanned(app, pilot)
                for i in (0, 2):
                    app._cur_node = regions[i]
                    app.action_select_region()
                got = {}
                app._extract = lambda rs: got.setdefault(
                    "offs", [r["offset"] for r in rs])
                app.action_extract_selected()
                assert got["offs"] == [app._regions[0]["offset"],
                                       app._regions[2]["offset"]]
        _run(scenario)

    def test_x_with_nothing_marked_says_what_to_do(self, blob):
        """"nothing happened" is the failure mode this whole change is about."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scanned(app, pilot)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.action_extract_selected()
                assert any("space" in n and "A" in n for n in notes), notes
        _run(scenario)

    def test_a_toggles_every_region(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scanned(app, pilot)
                app.action_select_all_regions()
                assert len(app._region_sel) == len(app._regions)
                app.action_select_all_regions()
                assert app._region_sel == set()
        _run(scenario)

    def test_space_still_pauses_a_running_scan(self, blob):
        """The key had a job before this and keeps it while a scan is live."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scanned(app, pilot)
                app._scanning = True
                before = set(app._region_sel)
                app._cur_node = [c for c in app.query_one("#tree").root.children
                                 if app._info(c) and app._info(c).region is not None][0]
                app.action_select_region()
                assert app._region_sel == before, (
                    "space changed the selection while a scan was running")
                app._scanning = False
        _run(scenario)


class TestTheFooterCarriesTheCommonKeys:
    def test_the_two_keys_that_drive_the_workflow_are_visible(self):
        """`l` opens the region list and `F` is what finds the NAMES in an
        archive. Both were show=False, so the two most useful keys in the tool
        were invisible."""
        shown = {}
        for b in AcidcatTUI.BINDINGS:
            if isinstance(b, tuple):
                shown[b[0]] = b[2] if len(b) > 2 else ""
            elif getattr(b, "show", False):
                shown[b.key] = b.description
        assert "l" in shown, "the region list is unreachable by discovery"
        assert "F" in shown, "nothing advertises how to get names"
        assert "question_mark" in shown

    def test_it_fits_a_terminal(self):
        """A footer wider than the screen teaches nothing: it is the one piece
        of documentation that is always on screen."""
        shown = [(b[0], b[2] if len(b) > 2 else "") if isinstance(b, tuple)
                 else (b.key, b.description)
                 for b in AcidcatTUI.BINDINGS
                 if isinstance(b, tuple) or getattr(b, "show", False)]
        width = len("  ".join(f"{k} {d}" for k, d in shown))
        assert width <= 170, f"the footer wants {width} columns"

    def test_region_actions_appear_only_once_there_are_regions(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                acts = ("select_region", "select_all_regions",
                        "extract_selected", "extract_all_regions")
                assert not any(app.check_action(a, ()) for a in acts)
                await _scanned(app, pilot)
                assert all(app.check_action(a, ()) for a in acts)
        _run(scenario)


class TestHelpIsReachableFromEverywhere:
    def test_the_region_list_has_its_own_help_key(self):
        assert "question_mark" in {b[0] for b in RegionsScreen.BINDINGS}

    def test_help_explains_the_three_screens_and_the_rule(self, blob):
        """The screens are pixel-identical apart from a title line, so the map
        has to be written down somewhere."""
        from textual.widgets import Static

        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(170, 50)) as pilot:
                await pilot.pause(0.3)
                app.action_help()
                await pilot.pause(0.3)
                text = app.screen_stack[-1].query_one(Static).content.plain
                assert "Three screens" in text
                assert "lowercase looks" in text
                for key in ("space", "X extract", "A ", "E "):
                    assert key.strip() in text, key
        _run(scenario)


class TestTheKeysThemselvesWork:
    """Pressed, not called.

    Every TUI bug this session had the same shape: the action existed, a test
    called it directly and passed, and the KEY never reached it. `tab` shipped
    unreachable that way. So these press, and test_tui_fundamentals redirects
    its gated-key exemption here on the strength of it.
    """

    async def _armed(self, app, pilot):
        """The state the region keys are live in: something located."""
        regions = await _scanned(app, pilot)
        tree = app.query_one("#tree")
        tree.focus()
        tree.move_cursor(regions[0])
        # Both of these, and in this order. The pause is not redundant with
        # the wait below: `until` returns immediately when its condition is
        # already true, so replacing the pause with it removed the tick the app
        # needs to drain pending work before a key arrives -- and every test
        # that pressed a key here started failing, deterministically, in the
        # direction the wait was supposed to prevent.
        #
        # A flat pause was doing two jobs. Waiting for focus is the one it
        # looked like it was doing; letting the app run is the one it was
        # actually load-bearing for.
        await pilot.pause()
        assert await until(
            pilot, lambda: tree.has_focus and tree.cursor_node is regions[0]), (
            "the tree never took focus with the cursor on the first region, "
            "so any key pressed from here lands somewhere else")
        return regions

    def test_pressing_space_marks(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await self._armed(app, pilot)
                assert await press_until(
                    pilot, "space", lambda: bool(app._region_sel)), (
                    "space did not reach the action after four presses")
        _run(scenario)

    def test_pressing_A_marks_them_all(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await self._armed(app, pilot)
                await pilot.press("A")
                await until(pilot,
                            lambda: len(app._region_sel) == len(app._regions))
                assert len(app._region_sel) == len(app._regions)
        _run(scenario)

    def test_pressing_X_extracts_the_marked_ones(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await self._armed(app, pilot)
                got = {}
                app._extract = lambda rs: got.setdefault("n", len(rs))
                # The subject here is X, not space. Marking via the action
                # rather than a keystroke removes a failure this test has no
                # opinion about: a dropped `space` left nothing marked, X
                # correctly refused, `_extract` was never called, and the
                # report read "X is broken" about a key that worked fine.
                # `test_pressing_space_marks` owns whether space arrives.
                app.action_select_region()
                await until(pilot, lambda: bool(app._region_sel))
                assert app._region_sel, "setup failed: nothing marked"
                await pilot.press("X")
                await until(pilot, lambda: "n" in got)
                assert got.get("n") == 1, got
        _run(scenario)

    def test_pressing_E_extracts_everything(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await self._armed(app, pilot)
                got = {}
                app._extract = lambda rs: got.setdefault("n", len(rs))
                await pilot.press("E")
                await until(pilot, lambda: "n" in got)
                assert got.get("n") == len(app._regions), got
        _run(scenario)
