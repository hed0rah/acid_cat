"""Every bound key must be reachable, and must say something when it declines.

Two defects found by actually pressing keys, which no reviewer had done -- the
Wave 1 audit recorded that all three stances skipped the TUI:

  `tab` was unreachable. Textual binds tab to focus_next at the screen level and
  consumed it first, so hex-edit mode could not be entered by the key its own
  help screen documents. `shift+tab` had the same problem.

  The hex pane could not be scrolled at all. Focus starts on the tree and never
  left it, so arrows always drove the tree, while #hexwrap sat there holding up
  to _HEX_CAP bytes -- far more than one screen.

And three keys did nothing while saying nothing, which is indistinguishable
from being broken. That is the same defect class as a silent cap, in the
smallest possible form.
"""

import asyncio
import os
import shutil

import pytest

from acidcat.tui_app.app import AcidcatTUI

pytest.importorskip("textual")

_BIG = os.path.join("data", "test_formats", "generated", "src.wav")
WAV = _BIG if os.path.isfile(_BIG) else os.path.join("data", "fixtures", "tone.wav")


@pytest.fixture
def wav(tmp_path):
    if not os.path.isfile(WAV):
        pytest.skip("test corpus WAV not present")
    p = tmp_path / "t.wav"
    shutil.copyfile(WAV, p)
    return str(p)


def _run(scenario):
    asyncio.run(scenario())


def test_tab_reaches_hex_edit(wav):
    """Textual's focus_next used to eat it. Only a real key press catches this:
    calling action_hex_focus() directly always worked."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert app._hexedit is not None, "tab did not enter hex edit"
    _run(scenario)


def test_arrows_move_the_hex_edit_cursor(wav):
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("tab")
            await pilot.pause()
            assert app._hexedit is not None
            for k in ("right", "right", "down"):
                await pilot.press(k)
            await pilot.pause()
            assert app._hexedit["cur"] > 0
    _run(scenario)


def test_the_hex_pane_can_be_focused_and_scrolled(wav):
    """It holds more than a screen and focus never reached it."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            # stay on the root node: the whole file, so the pane holds
            # _HEX_CAP bytes and genuinely overflows one screen
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app._focused_pane() == "hexwrap", "shift+tab skipped the hex pane"
            hw = app.query_one("#hexwrap")
            assert hw.max_scroll_y > 0, "the pane should hold more than one screen"
            for _ in range(6):
                await pilot.press("down")
            await pilot.pause()
            assert hw.scroll_offset.y > 0, "arrows did not scroll the focused pane"
    _run(scenario)


def test_zoom_zooms_the_focused_pane_not_a_fixed_order(wav):
    """Reported: z walked its own sequence instead of zooming what you were
    looking at."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("z")             # tree has focus at start
            await pilot.pause()
            assert app._zoom == "zoom-tree"
            await pilot.press("z")             # toggles off
            await pilot.pause()
            assert app._zoom is None
            await pilot.press("shift+tab")     # now the hex pane
            await pilot.press("z")
            await pilot.pause()
            assert app._zoom == "zoom-hex"
            assert app.query_one("#hexwrap").size.width >= 76
    _run(scenario)


def test_zoom_focuses_what_it_zoomed(wav):
    """Zooming a pane you then cannot drive is half a feature."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.press("z")
            await pilot.pause()
            assert app._focused_pane() == "hexwrap"
    _run(scenario)


@pytest.mark.parametrize("key,expect", [
    ("full_stop", "nothing is playing"),
    ("u", "not inside a region"),
    ("ctrl+t", "no field is being edited"),
])
def test_a_key_that_declines_says_why(wav, key, expect):
    """These three changed nothing and printed nothing. A key that is silent
    when it refuses is indistinguishable from one that is broken -- which is
    precisely how the unreachable tab presented."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            notes = []
            app.notify = lambda m, **kw: notes.append(str(m))
            await pilot.press(key)
            await pilot.pause()
            assert any(expect in n for n in notes), f"{key} said {notes}"
    _run(scenario)


def test_stopping_playback_internally_stays_quiet(wav):
    """The three internal callers are cleanup before a new sound, not a user
    pressing '.', so they must not narrate."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            notes = []
            app.notify = lambda m, **kw: notes.append(str(m))
            app.action_stop_play(quiet=True)
            await pilot.pause()
            assert not [n for n in notes if "nothing is playing" in n]
    _run(scenario)


def test_expand_and_collapse_actually_move_the_tree(wav):
    """Guarding against a harness blind spot: a sweep that only watches scalar
    app state calls these dead, because what they change is the tree."""
    def expanded(app):
        n = 0
        def walk(node):
            nonlocal n
            if node.is_expanded:
                n += 1
            for c in node.children:
                walk(c)
        walk(app.query_one("#tree").root)
        return n

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            base = expanded(app)
            await pilot.press("a")
            await pilot.pause()
            opened = expanded(app)
            assert opened > base
            await pilot.press("c")
            await pilot.pause()
            assert expanded(app) < opened
    _run(scenario)
