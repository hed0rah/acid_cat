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


def test_the_hex_pane_does_not_fit_a_row_unzoomed(wav):
    """The bug this exists to fix. If this ever fails because the layout got
    wider, the zoom is still useful but this rationale needs rewriting."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#hexwrap").size.width < ROW_COLUMNS
    _run(scenario)


def test_zoom_gives_the_hex_pane_the_whole_screen(wav):
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("z")
            await pilot.pause()
            assert app.query_one("#hexwrap").size.width >= ROW_COLUMNS
    _run(scenario)


def test_zoom_cycles_every_pane_and_returns(wav):
    """z walks hex -> tree -> anomalies -> off. Each step must actually give
    that pane the width, not merely set a class."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            start = app.query_one("#hexwrap").size.width

            await pilot.press("z"); await pilot.pause()
            assert app._zoom == "zoom-hex"
            assert app.query_one("#hexwrap").size.width >= ROW_COLUMNS

            await pilot.press("z"); await pilot.pause()
            assert app._zoom == "zoom-tree"
            assert app.query_one("#tree").size.width >= ROW_COLUMNS

            await pilot.press("z"); await pilot.pause()
            assert app._zoom == "zoom-anom"
            assert app.query_one("#anomwrap").size.width >= ROW_COLUMNS

            await pilot.press("z"); await pilot.pause()
            assert app._zoom is None
            assert app.query_one("#hexwrap").size.width == start
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
