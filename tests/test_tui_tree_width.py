"""A deep branch that runs off the pane has to be reachable.

The tree earned a fourth level, and a fourth level in a split pane is where
indentation stops being free. Two things follow from that, and only one of them
was already true:

  Textual's `overflow-x: auto` does put a horizontal scrollbar there on its own,
  and nothing on the keyboard could move it -- the scrollbar was visible proof
  that there was more to see and no way to see it. The keys for it have to be
  ctrl+arrows, because Tree already spends shift+left/right on jump-to-parent
  and jump-to-next-ancestor, which matter more the deeper the tree gets.

  Four columns of indent per level is fine two deep. Four deep in a 36-column
  pane it spends a third of the width on guides.
"""

import asyncio
import struct

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI      # noqa: E402


def _run(scenario):
    asyncio.run(scenario())


@pytest.fixture
def wav(tmp_path):
    """Wide enough to overflow a narrow pane: the summary line does that on its
    own, without needing the region machinery."""
    n = 6000
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", n) + b"\x02" * n)
    p = tmp_path / "wide.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


async def _wide_open(app, pilot):
    tree = app.query_one("#tree")
    for c in list(tree.root.children):
        if c.allow_expand:
            c.expand()
    await pilot.pause(0.2)
    tree.focus()
    await pilot.pause()
    return tree


class TestPanningTheTree:
    def test_ctrl_arrows_move_the_view_sideways(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(80, 30)) as pilot:
                await pilot.pause(0.3)
                tree = await _wide_open(app, pilot)
                assert tree.max_scroll_x > 0, (
                    "fixture does not overflow this pane, so panning it proves "
                    "nothing")
                await pilot.press("ctrl+right")
                await pilot.pause()
                assert tree.scroll_offset.x > 0, "ctrl+right did not pan"
                await pilot.press("ctrl+left")
                await pilot.pause()
                assert tree.scroll_offset.x == 0
        _run(scenario)

    def test_it_stops_at_the_left_edge_and_says_so(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(80, 30)) as pilot:
                await pilot.pause(0.3)
                tree = await _wide_open(app, pilot)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                for _ in range(3):
                    await pilot.press("ctrl+left")
                    await pilot.pause()
                assert tree.scroll_offset.x == 0
                assert any("edge" in n for n in notes), notes
        _run(scenario)

    def test_a_tree_that_fits_declines_out_loud(self, tmp_path):
        """The invariant the whole app is held to: a key that moves nothing and
        says nothing is indistinguishable from a broken build."""
        p = tmp_path / "tiny.wav"
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
                + struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
                + b"data" + struct.pack("<I", 8) + b"\x00" * 8)
        p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

        async def scenario():
            app = AcidcatTUI(str(p))
            async with app.run_test(size=(200, 40)) as pilot:
                await pilot.pause(0.3)
                tree = app.query_one("#tree")
                tree.focus()
                await pilot.pause()
                assert tree.max_scroll_x == 0, "fixture overflows after all"
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                await pilot.press("ctrl+right")
                await pilot.pause()
                assert any("nothing to pan" in n for n in notes), notes
        _run(scenario)

    def test_it_did_not_cost_the_tree_its_own_shift_arrows(self, wav):
        """`shift+left` is Tree's jump-to-parent. Binding the pan there would
        have paid for sideways movement with the one key that gets you out of a
        deep branch -- a straight downgrade in the exact case this is for."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(80, 30)) as pilot:
                await pilot.pause(0.3)
                tree = await _wide_open(app, pilot)
                node = [c for c in tree.root.children if c.allow_expand][0]
                child = node.children[-1]
                tree.move_cursor(child)
                await pilot.pause()
                await pilot.press("shift+left")
                await pilot.pause()
                assert tree.cursor_node is node, "shift+left no longer walks up"
                assert tree.scroll_offset.x == 0, "shift+left panned instead"
        _run(scenario)

    def test_a_modal_swallows_it(self, wav):
        """Every app-global binding is dormant under a modal, and this one is
        an arrow key, so a form that ignored it would be worse than most."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(80, 30)) as pilot:
                await pilot.pause(0.3)
                await _wide_open(app, pilot)
                app.action_help()
                await pilot.pause(0.2)
                assert app.check_action("tree_pan", (8,)) is False
        _run(scenario)


class TestTheGuidesAreNarrow:
    def test_two_columns_per_level_not_four(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(80, 30)) as pilot:
                await pilot.pause(0.3)
                assert app.query_one("#tree").guide_depth == 2
        _run(scenario)
