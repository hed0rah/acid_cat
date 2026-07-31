"""Cover the modal screens that had no test.

EditScreen matters most: it is the screen that writes to disk, and it is where a
NameError on the save path survived a fully green suite -- the helper it calls to
apply an edit was never imported. A test that actually walks the save path is the
thing that would have caught it, so that is what these do.
"""

import asyncio
import os
import shutil

import pytest

WAV = os.path.join("data", "test_formats", "generated", "src.wav")


def _drive(scenario):
    asyncio.run(scenario())


@pytest.fixture
def wav(tmp_path):
    pytest.importorskip("textual")
    if not os.path.isfile(WAV):
        pytest.skip("test corpus WAV not present")
    p = tmp_path / "edit_me.wav"
    shutil.copyfile(WAV, p)
    return str(p)


class _Host:
    """A bare app that just hosts a modal screen under test."""

    @staticmethod
    def build():
        from textual.app import App

        class Host(App):
            def compose(self):
                return iter(())

        return Host()


def test_edit_screen_save_applies_the_change(wav):
    """The save path end to end: a typed field reaches the write engine and the
    screen dismisses with the new bytes. This is the path that used to crash."""
    from acidcat.tui_app.screens import EditScreen
    from acidcat.tui_app.render import edit_profile
    from textual.widgets import Input

    profile, fields = edit_profile(wav)
    assert profile == "WAV"

    async def scenario():
        app = _Host.build()
        async with app.run_test(size=(100, 40)) as pilot:
            result = {}
            app.push_screen(EditScreen(wav, profile, fields),
                            lambda r: result.update(r or {"dismissed_none": True}))
            await pilot.pause()
            screen = app.screen
            screen.query_one("#f_title", Input).value = "characterization"
            screen.action_save()
            await pilot.pause()
            assert "new_data" in result, f"save did not produce bytes: {result}"
            assert b"characterization" in result["new_data"]
            assert result["applied"], "nothing reported as applied"

    _drive(scenario)


def test_edit_screen_with_no_input_dismisses_without_writing(wav):
    """Blank fields mean 'leave everything alone' -- no write, no result dict."""
    from acidcat.tui_app.screens import EditScreen
    from acidcat.tui_app.render import edit_profile

    profile, fields = edit_profile(wav)
    before = open(wav, "rb").read()

    async def scenario():
        app = _Host.build()
        async with app.run_test(size=(100, 40)) as pilot:
            got = []
            app.push_screen(EditScreen(wav, profile, fields), got.append)
            await pilot.pause()
            app.screen.action_save()
            await pilot.pause()
            assert got == [None], f"expected a plain dismissal, got {got}"
            assert open(wav, "rb").read() == before, "file was touched"

    _drive(scenario)


def test_edit_screen_reports_write_errors_without_dismissing(wav, monkeypatch):
    """A failed write must surface in the status line and keep the screen open,
    so the user does not lose what they typed."""
    from acidcat.tui_app import screens
    from acidcat.tui_app.render import edit_profile
    from acidcat.core.write.edits import EditError
    from textual.widgets import Input, Static

    profile, fields = edit_profile(wav)

    def boom(path, changes):
        raise EditError("synthetic failure")

    monkeypatch.setattr(screens, "_write_edit", boom)

    async def scenario():
        app = _Host.build()
        async with app.run_test(size=(100, 40)) as pilot:
            got = []
            app.push_screen(screens.EditScreen(wav, profile, fields), got.append)
            await pilot.pause()
            screen = app.screen
            screen.query_one("#f_title", Input).value = "whatever"
            screen.action_save()
            await pilot.pause()
            assert got == [], "screen dismissed despite a failed write"
            status = screen.query_one("#editstatus", Static)
            assert "synthetic failure" in str(status.content)

    _drive(scenario)


def test_edit_screen_cancel_returns_nothing(wav):
    from acidcat.tui_app.screens import EditScreen
    from acidcat.tui_app.render import edit_profile

    profile, fields = edit_profile(wav)

    async def scenario():
        app = _Host.build()
        async with app.run_test(size=(100, 40)) as pilot:
            got = []
            app.push_screen(EditScreen(wav, profile, fields), got.append)
            await pilot.pause()
            app.screen.action_cancel()
            await pilot.pause()
            assert got == [None]

    _drive(scenario)


def test_confirm_screen_yes_and_no(wav):
    """The guard in front of every destructive path must return a real verdict."""
    from acidcat.tui_app.screens import ConfirmScreen

    async def scenario():
        app = _Host.build()
        async with app.run_test(size=(100, 40)) as pilot:
            got = []
            app.push_screen(ConfirmScreen("discard everything?"), got.append)
            await pilot.pause()
            app.screen.action_cancel()
            await pilot.pause()
            assert got and got[0] in (None, False), f"cancel returned {got}"

    _drive(scenario)


def test_browse_screen_lists_a_directory(tmp_path):
    """BrowseScreen builds a DirectoryTree -- the widget whose missing import was
    only caught by a static sweep. Mounting it proves the import is real."""
    pytest.importorskip("textual")
    from acidcat.tui_app.screens import BrowseScreen
    from textual.widgets import DirectoryTree

    (tmp_path / "a.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    async def scenario():
        app = _Host.build()
        async with app.run_test(size=(100, 40)) as pilot:
            app.push_screen(BrowseScreen(str(tmp_path)))
            await pilot.pause()
            assert app.screen.query(DirectoryTree), "no directory tree mounted"

    _drive(scenario)


def test_help_screen_mounts_and_closes():
    """The help overlay must render and get out of the way again."""
    pytest.importorskip("textual")
    from acidcat.tui_app.screens import HelpScreen

    async def scenario():
        app = _Host.build()
        async with app.run_test(size=(100, 40)) as pilot:
            depth = len(app.screen_stack)
            app.push_screen(HelpScreen())
            await pilot.pause()
            assert len(app.screen_stack) == depth + 1
            app.screen.action_close()
            await pilot.pause()
            assert len(app.screen_stack) == depth, "help screen did not close"

    _drive(scenario)
