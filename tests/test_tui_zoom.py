"""A pane has to be able to own the screen.

A hex row is exactly 76 columns: 10 of gutter, 48 of hex, the mid-gap, and 16
of ascii. The right pane is 52% of the terminal (app.py CSS), so the grid needs
roughly a 154-column terminal to fit. At 120 the pane is 61 wide and
`#hexwrap` is a VerticalScroll with no horizontal scrolling, so the dump folds.

That was true before any of this and nothing surfaced it, because nothing
measured the row against the pane.

Textual can maximize natively, but only for widgets whose read-only
`allow_maximize` is true -- which excludes these containers -- and it postdates
the `textual>=0.60` floor declared in pyproject. CSS classes work on every
version and need no feature test.
"""

import asyncio
import os
import shutil

import pytest

from acidcat.tui_app.app import AcidcatTUI

pytest.importorskip("textual")

_BIG = os.path.join("data", "test_formats", "generated", "src.wav")
WAV = _BIG if os.path.isfile(_BIG) else os.path.join("data", "fixtures", "tone.wav")

ROW_COLUMNS = 76          # what _hex_rows actually emits; see render.py


@pytest.fixture
def wav(tmp_path):
    if not os.path.isfile(WAV):
        pytest.skip("test corpus WAV not present")
    p = tmp_path / "t.wav"
    shutil.copyfile(WAV, p)
    return str(p)


def _run(scenario):
    asyncio.run(scenario())


def test_a_narrow_pane_narrows_the_row_instead_of_folding(wav):
    """The grid does not scroll horizontally, so a row wider than the pane
    wraps and column position stops meaning anything. The width follows the
    pane now, the way `od --width` always could."""
    from acidcat.tui_app.render import row_width_for
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#hexwrap").size.width
            w = app._hex_width()
            need = 10 + 3 * w + (1 if w > 8 else 0) + 1 + w
            assert w < 16, "a 100-column terminal cannot fit 16 bytes per row"
            assert need <= pane, f"{w}/row needs {need}, pane is {pane}"
    _run(scenario)


def test_row_width_thresholds():
    from acidcat.tui_app.render import row_width_for
    assert row_width_for(76) == 16
    assert row_width_for(75) == 8
    assert row_width_for(43) == 8
    assert row_width_for(42) == 4


def test_zoom_gives_the_hex_pane_the_whole_screen(wav):
    """Reach the pane first: zoom acts on what has focus, and focus starts on
    the tree. That is the correction to the first version of this feature,
    which walked a fixed order and ignored where you were."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("z")
            await pilot.pause()
            assert app.query_one("#hexwrap").size.width >= ROW_COLUMNS
    _run(scenario)


def test_zoom_reaches_every_pane_and_toggles_back(wav):
    """Each pane can own the screen, and z on an already-zoomed pane restores
    the layout rather than moving to the next one."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            start = app.query_one("#hexwrap").size.width

            await pilot.press("z"); await pilot.pause()          # tree is focused
            assert app._zoom == "zoom-tree"
            assert app.query_one("#tree").size.width >= ROW_COLUMNS
            await pilot.press("z"); await pilot.pause()
            assert app._zoom is None
            assert app.query_one("#hexwrap").size.width == start

            await pilot.press("tab")                        # -> hex pane
            await pilot.press("z"); await pilot.pause()
            assert app._zoom == "zoom-hex"
            assert app.query_one("#hexwrap").size.width >= ROW_COLUMNS
            await pilot.press("z"); await pilot.pause()

            # forensics no longer zooms: it sits in the left column now, sized
            # to its content, and over 900 real files 894 have nothing to show.
            assert "anomwrap" not in app._PANES
    _run(scenario)


def test_zoom_is_reachable_from_the_footer_and_the_help(wav):
    """A navigation key nobody can find is not a navigation key."""
    shown = {}
    for b in AcidcatTUI.BINDINGS:
        if isinstance(b, tuple):
            shown[b[0]] = b[2]
        elif getattr(b, "show", True):
            shown[b.key] = b.description
    assert "z" in shown
    assert len(shown) <= 14, f"footer shows {len(shown)}, too many to read"

    from acidcat.tui_app.screens import HelpScreen
    import inspect
    assert '("z"' in inspect.getsource(HelpScreen)


def test_the_hex_row_never_wraps_at_any_width(wav):
    """The row must fit the space it actually gets, at every terminal size and
    on every node -- including one long enough to raise a scrollbar.

    This is the regression that made wrapping look random. Scrollbar presence
    depends on content height, which depends on the row width, so measuring the
    live scrollbar lands one layout behind: a long chunk lost two columns after
    the width was chosen, and stepping to a short field and back flipped the
    stale measurement and appeared to fix it.
    """
    async def scenario():
        for cols in (80, 100, 120, 140, 156, 158, 160, 164, 172, 200, 240):
            app = AcidcatTUI(wav)
            async with app.run_test(size=(cols, 44)) as pilot:
                await pilot.pause()
                pane = app.query_one("#hex")
                for _ in range(5):
                    await pilot.pause()
                    w = app._hex_width()
                    need = 10 + 3 * w + (1 if w > 8 else 0) + 1 + w
                    got = pane.content_size.width
                    assert need <= got, (
                        f"{cols} cols: {w}/row needs {need}, pane gives {got}")
                    await pilot.press("down")
    _run(scenario)


def test_the_detail_pane_never_changes_height(wav):
    """The layout must not jump as you move through the tree.

    #detail was `height: auto`, so a summary long enough to wrap grew the pane
    and stole a row from the hex view below it -- the visible symptom being
    that the hex pane resized when you selected a different chunk. It is a
    two-line status now, fixed, and a long line clips.
    """
    async def scenario():
        for cols in (100, 120, 140, 200):
            app = AcidcatTUI(wav)
            async with app.run_test(size=(cols, 44)) as pilot:
                await pilot.pause()
                d = app.query_one("#detail")
                seen = set()
                for _ in range(8):
                    await pilot.press("down")
                    await pilot.pause()
                    seen.add(d.content_size.height)
                assert len(seen) == 1, (
                    f"{cols} cols: #detail took heights {sorted(seen)} across "
                    f"nodes -- the layout jumps")
    _run(scenario)


def test_a_summary_does_not_repeat_the_size_the_row_shows(wav):
    """The row prints the size, then the walker summary printed it again."""
    from acidcat.tui_app.render import trim_size_echo
    assert trim_size_echo("audio payload, 176,400 bytes, 1.000 s", 176400) \
        == "audio payload, 1.000 s"
    assert trim_size_echo("padding, 8,192 bytes", 8192) == "padding"
    # a DIFFERENT number is a real statement -- a declared size, a payload
    # inside a larger chunk -- and must survive
    assert trim_size_echo("declared 999 bytes", 12) == "declared 999 bytes"
    assert trim_size_echo("INFO, 1 entries", 26) == "INFO, 1 entries"


def test_no_tree_row_states_its_size_twice(wav):
    """The call site, not the helper. The row prints the size itself and then
    the walker's summary printed it again -- 'data 0x46 176,400b audio payload,
    176,400 bytes'. Two statements of one fact, and on the widest chunks it is
    what pushed the row past the pane."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(200, 44)) as pilot:
            await pilot.pause()
            tree = app.query_one("#tree")
            checked = 0
            for node in [tree.root] + list(tree.root.children):
                lbl = node.label
                text = lbl.plain if hasattr(lbl, "plain") else str(lbl)
                meta = app._nodemeta.get(id(node))
                if not meta:
                    continue
                size = meta[1]
                checked += 1
                assert f"{size:,}b" not in text or f"{size:,} bytes" not in text, (
                    f"row states its size twice: {text!r}")
            assert checked, "no chunk rows were checked"
    _run(scenario)
