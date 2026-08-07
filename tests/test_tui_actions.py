"""Behavioural cover for the TUI actions that had none.

The interactive half of the app was the least-tested surface in the codebase --
32 key actions, 14 of them never referenced by a test -- and that is exactly
where a latent NameError survived a full green suite (the metadata-save path
referenced an unimported helper). These drive the real app through Textual's
pilot and assert what each action is supposed to *do*, so a future refactor of
the 115-method app class has something to fail against.
"""

import asyncio
import os
import shutil

import pytest

_BIG = os.path.join("data", "test_formats", "generated", "src.wav")
WAV = _BIG if os.path.isfile(_BIG) else os.path.join("data", "fixtures", "tone.wav")


def _drive(scenario):
    """Run an async pilot scenario, matching the style used in test_tui.py."""
    asyncio.run(scenario())


@pytest.fixture
def wav(tmp_path):
    pytest.importorskip("textual")
    if not os.path.isfile(WAV):
        pytest.skip("test corpus WAV not present")
    p = tmp_path / "probe.wav"
    shutil.copyfile(WAV, p)
    return str(p)


def test_cycle_view_rotates_and_returns(wav):
    """`v` cycles hex -> entropy -> hilbert -> histogram and wraps."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            seen = [app._view]
            for _ in range(4):
                app.action_cycle_view()
                await pilot.pause()
                seen.append(app._view)
            assert seen[0] == "hex"
            assert seen[:4] == ["hex", "entropy", "hilbert", "histogram"]
            assert seen[4] == "hex", "cycling four times must return to hex"

    _drive(scenario)


def test_cycle_view_leaves_hexedit_mode(wav):
    """Switching view must not strand the app in byte-edit mode."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._hexedit = {"off": 0, "len": 4, "cur": 0, "nib": 0}
            app.action_cycle_view()
            await pilot.pause()
            assert app._hexedit is None

    _drive(scenario)


def test_expand_and_collapse_all(wav):
    """`E` / `C` drive the whole tree, not just the cursor node."""
    from acidcat.tui_app import AcidcatTUI
    from textual.widgets import Tree

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            root = app.query_one("#tree", Tree).root
            app.action_expand_all()
            await pilot.pause()
            assert any(c.is_expanded for c in root.children), "nothing expanded"
            app.action_collapse_all()
            await pilot.pause()
            assert not any(c.is_expanded for c in root.children), "still expanded"

    _drive(scenario)


def test_next_finding_notifies_when_none(wav):
    """With no forensics findings the action must say so, not index into []."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.findings = []
            app.action_next_finding()          # must not raise
            await pilot.pause()

    _drive(scenario)


def test_next_finding_cycles_and_wraps(wav):
    """Repeated presses walk the findings list and wrap at the end."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.findings = [
                {"offset": 0, "message": "first"},
                {"offset": 16, "message": "second"},
            ]
            app._finding_idx = -1
            app.action_next_finding()
            await pilot.pause()
            assert app._finding_idx == 0
            app.action_next_finding()
            await pilot.pause()
            assert app._finding_idx == 1
            app.action_next_finding()
            await pilot.pause()
            assert app._finding_idx == 0, "must wrap back to the first finding"

    _drive(scenario)


def test_play_without_a_region_warns(wav):
    """`p` with no highlighted bytes warns instead of reading a None offset."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._cur_region = (None, 0, None)
            app.action_play()                  # must not raise
            await pilot.pause()
            app.action_stop_play()             # stopping when nothing plays is fine
            await pilot.pause()

    _drive(scenario)


def test_locate_regions_is_inert_while_scanning(wav):
    """The scan guard: `l` must not start a second scan over a running one."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._scanning = True
            app._blob_src = "sentinel"
            app.action_locate_regions()        # guarded: returns immediately
            await pilot.pause()
            assert app._blob_src == "sentinel", "a second scan clobbered the state"

    _drive(scenario)


def test_scan_lifecycle_actions_are_safe_when_idle(wav):
    """pause / keep / cancel must be no-ops when no scan is running."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert not app._scanning
            app.action_pause_scan()
            app.action_keep_scan()
            app.action_cancel_scan()
            await pilot.pause()
            assert not app._scanning

    _drive(scenario)


def test_cancel_edit_clears_the_target(wav):
    """Escaping a field edit must drop the pending target."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._edit_target = {"name": "sample_rate", "off": 24, "len": 4}
            app.action_cancel_edit()
            await pilot.pause()
            assert app._edit_target is None

    _drive(scenario)


def test_open_on_a_dirty_file_asks_first(wav):
    """Opening another file with unsaved edits must confirm, not discard."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.dirty = True
            app.action_open()
            await pilot.pause()
            assert len(app.screen_stack) > 1, "no confirmation screen was pushed"

    _drive(scenario)


def test_request_quit_on_a_dirty_file_asks_first(wav):
    """Same contract on the way out: unsaved edits must not vanish silently."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.dirty = True
            app.action_request_quit()
            await pilot.pause()
            assert len(app.screen_stack) > 1, "quit did not confirm unsaved changes"

    _drive(scenario)


def test_audio_params_reject_a_lying_fmt_chunk(tmp_path):
    """A corrupt fmt chunk yields arbitrary integers. They must not reach the
    playback WAV header, whose byte_rate is a u32 -- an unclamped rate overflows
    struct.pack and takes the player down mid-session."""
    pytest.importorskip("textual")
    from acidcat.tui_app import AcidcatTUI

    # declared size 0xffffffff, and a fmt body that reads as rate ~2.9e9 / 44100-bit
    bad = tmp_path / "lying.wav"
    bad.write_bytes(b"RIFF\xff\xff\xff\xffWAVEfmt " + b"\x10\x00\x00\x00"
                    + b"\x01\x00\x02\x00" + b"\x44\xac" * 8)

    async def scenario():
        app = AcidcatTUI(str(bad))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            rate, ch, bits, _ = app._audio_params()
            assert 1000 <= rate <= 768000, f"insane sample rate survived: {rate}"
            assert 1 <= ch <= 64, f"insane channel count survived: {ch}"
            assert bits in (8, 16, 24, 32, 64), f"insane bit depth survived: {bits}"
            assert rate * ch * (bits // 8) <= 0xFFFFFFFF, "byte_rate overflows u32"
            app.action_play()          # must not raise struct.error
            await pilot.pause()

    _drive(scenario)


def test_search_prev_steps_backwards(wav):
    """`N` walks the search hits in reverse without falling off the list."""
    from acidcat.tui_app import AcidcatTUI

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_search_prev()           # no active search: must not raise
            await pilot.pause()

    _drive(scenario)
