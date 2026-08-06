"""Opening a file must leave the keyboard pointed at the tree.

The TUI docks a hidden Input (`#editbar`) for goto/search prompts. Textual still
treats it as focusable even while `display: none`, so if nothing claims focus on
open it wins by default -- and then every single-key binding and every arrow key
is swallowed by an invisible text box. The app looks dead on the first keypress,
which is the first thing anyone does.

The scan path already worked around this (`action_locate_regions` focuses the
tree explicitly); the ordinary open path did not.
"""

import asyncio
import math
import struct

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI  # noqa: E402


def _wav(path, frames=2000):
    pcm = b"".join(struct.pack("<h", int(9000 * math.sin(i / 30.0)))
                   for i in range(frames))
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


def _scenario(tmp_path, body):
    """Run `body(app, pilot)` against a freshly opened WAV."""
    src = _wav(tmp_path / "focus.wav")
    result = {}

    async def go():
        app = AcidcatTUI(src)
        async with app.run_test(size=(120, 40)) as pilot:
            await body(app, pilot, result)

    asyncio.run(go())
    return result


def test_the_tree_has_focus_after_open(tmp_path):
    async def body(app, pilot, out):
        out["id"] = getattr(app.focused, "id", None)

    assert _scenario(tmp_path, body)["id"] == "tree"


def test_arrows_move_the_cursor(tmp_path):
    """The symptom a user hits first: press Down, nothing happens."""
    async def body(app, pilot, out):
        tree = app.query_one("#tree")
        out["before"] = tree.cursor_line
        await pilot.press("down")
        out["after"] = tree.cursor_line

    r = _scenario(tmp_path, body)
    assert r["after"] != r["before"], "Down did not move the tree cursor"


def test_single_key_bindings_reach_the_app(tmp_path):
    """'?' is the canonical one -- it is the only complete key reference, so if
    it does not open, a stuck user has no way back in."""
    async def body(app, pilot, out):
        await pilot.press("question_mark")
        out["screen"] = type(app.screen).__name__

    assert _scenario(tmp_path, body)["screen"] == "HelpScreen"
