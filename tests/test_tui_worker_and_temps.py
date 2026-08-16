"""Two lifecycle rules the frame model states and two places that broke them.

Both were found by review rather than by use, and both are the kind that leave
no trace at the moment they happen.

THE STALE WORKER. Exploration runs on a thread, so its answer can arrive after
the view it was computed for has gone. The guard was "removing the placeholder
raises if the tree was rebuilt". It does not raise, and believing it did was
worse than having no guard at all. Measured against Textual 8.2.8:

    Tree.clear() builds a NEW root and resets _current_id to 0 -- and does NOT
    clear _tree_nodes. The stale node is still listed by its own detached
    parent, so remove() succeeds; its last statement is
    `del self._tree._tree_nodes[self.id]`, and the rebuilt tree has already
    handed that id to a live node. The guard deleted the node it was protecting.

Then _explore_apply ran anyway, binding detached nodes into the new view's
_pathnode and _allnodes, where goto, search and cursor-restore can resolve to
them. And the worker read `self.work` at execution time, so after a descend or
a `u` it walked a different file at the old offsets.

THE LEAKED FRAMES. Every frame owns a working copy and, once descended, a
carved source. `_open_path` drops the whole stack for exactly this reason.
`on_unmount` -- the other way out of a session -- freed only the current working
copy, so quitting after any descend left one carved region per descend in the
temp directory, silently.
"""

import asyncio
import os
import struct

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI      # noqa: E402


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
def wav(tmp_path):
    """Rebuilds into many nodes, so `clear()` hands the same ids back out."""
    n = 4000
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", n) + b"A" * n)
    p = tmp_path / "many.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


@pytest.fixture
def blob(tmp_path):
    p = tmp_path / "frames.blob"
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


class TestAnAnswerForAViewThatIsGone:
    def test_a_rebuild_moves_the_generation(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                before = app._generation
                app._load()
                await pilot.pause(0.2)
                assert app._generation > before, (
                    "nothing marks the old view as gone, so a worker cannot "
                    "tell whether its answer still applies")
        _run(scenario)

    def test_a_stale_result_does_not_pollute_the_new_view(self, wav):
        """Measured on the right thing. Counting nodes in the fresh tree does
        NOT catch this -- the stale apply hangs its children on the DETACHED
        node, so the visible tree is unchanged while `_allnodes` and
        `_pathnode` quietly gain entries pointing outside it. Those two are
        what goto, search and cursor-restore resolve against, so that is where
        the damage lands."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                tree = app.query_one("#tree")
                node = tree.root.children[0]
                placeholder = node.add_leaf("looking inside...")
                stale_gen = app._generation

                app._load()
                await pilot.pause(0.3)
                nodes_before = len(app._allnodes)
                paths_before = len(app._pathnode)

                app._explore_landed(
                    node, placeholder,
                    {"engine": "walker", "label": "stale",
                     "chunks": [{"id": "GHST", "offset": 0, "size": 32,
                                 "payload_base": 8, "payload_len": 24,
                                 "extent_len": 32, "geometry": "declared",
                                 "fields": [], "summary": ""}],
                     "regions": [], "warnings": [], "note": None},
                    0, 4096, 1, stale_gen)
                await pilot.pause(0.2)

                assert len(app._allnodes) == nodes_before, (
                    "a stale answer registered nodes that are not in the tree; "
                    "goto and search can now land on them")
                assert len(app._pathnode) == paths_before, (
                    "a stale answer registered paths into the live view")
        _run(scenario)

    def test_a_stale_answer_never_reaches_remove_at_all(self, wav):
        """The guard has to short-circuit BEFORE touching the node, because
        touching it is the damage.

        Asserted by watching the call rather than by arranging an id collision:
        collisions need the rebuilt tree to be larger than the stale node's id,
        which is real in use and fiddly to stage -- an earlier version of this
        test staged it badly, found no collision, and passed without asserting
        anything. That `remove()` corrupts when it does collide is a property
        of Textual, pinned separately below.
        """
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                tree = app.query_one("#tree")
                placeholder = tree.root.children[0].add_leaf("looking inside...")
                stale_gen = app._generation
                called = []
                placeholder.remove = lambda: called.append(True)

                app._load()
                await pilot.pause(0.3)
                app._explore_landed(
                    tree.root, placeholder,
                    {"engine": None, "label": None, "chunks": [],
                     "regions": [], "warnings": [], "note": None},
                    0, 100, 1, stale_gen)
                await pilot.pause(0.2)
                assert not called, (
                    "a stale answer still called remove() on a detached node")
        _run(scenario)

    def test_textual_really_does_reuse_ids_after_a_clear(self):
        """Why the old guard could not work, pinned against the library rather
        than described. If a future Textual clears _tree_nodes or stops resetting
        _current_id, this fails and the generation counter can be revisited."""
        from textual.app import App, ComposeResult
        from textual.widgets import Tree

        class _T(App):
            def compose(self) -> ComposeResult:
                yield Tree("root", id="t")

        async def scenario():
            app = _T()
            async with app.run_test() as pilot:
                await pilot.pause()
                t = app.query_one("#t", Tree)
                stale = t.root.add_leaf("stale")
                t.clear()
                live = t.root.add_leaf("live")
                assert live.id == stale.id, (
                    "ids are no longer reused; the old guard's premise changed")
                stale.remove()          # must not raise -- that was the premise
                assert live.id not in t._tree_nodes, (
                    "removing a stale node no longer unregisters the live one")
        _run(scenario)

    def test_a_current_result_is_still_applied(self, blob):
        """The guard must not be so eager that the ordinary case stops working."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scanned(app, pilot)
                tree = app.query_one("#tree")
                node = [c for c in tree.root.children
                        if app._info(c) and app._info(c).region is not None][0]
                node.expand()
                for _ in range(80):
                    if node.children and not any(
                            "looking inside" in app._node_name(c)
                            for c in node.children):
                        break
                    await pilot.pause(0.05)
                assert node.children, "the ordinary path stopped delivering"
                assert not any("looking inside" in app._node_name(c)
                               for c in node.children)
        _run(scenario)


class TestQuittingFreesWhatTheFramesOwn:
    def test_no_temp_survives_a_descend_then_quit(self, blob, tmp_path):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scanned(app, pilot)
                app._descend(0)
                await pilot.pause(0.3)
                owned = [p for p in (app.src, app.work)
                         if p and os.path.isfile(p)]
                assert owned, "the descend produced no temp to leak"
                assert app.carved, "this view does not own its source"
            # the context manager unmounts the app, which is `q`
            left = [p for p in owned if os.path.isfile(p)]
            assert not left, f"quitting leaked {left}"
        _run(scenario)

    def test_the_whole_stack_is_freed_not_just_the_top(self, blob):
        """Two levels down leaks the intermediate frame too, and that one is
        invisible from the last view you were looking at."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scanned(app, pilot)
                app._descend(0)
                await pilot.pause(0.3)
                mid = app.src
                app._regions = [{"kind": "carve", "format": None,
                                 "offset": 10, "end": 900, "length": 890}]
                app._blob_src = app.src
                app._descend(0)
                await pilot.pause(0.3)
                deep = app.src
                assert os.path.isfile(mid) and os.path.isfile(deep)
                watched = [mid, deep]
            left = [p for p in watched if os.path.isfile(p)]
            assert not left, f"quitting left {len(left)} frame temp(s): {left}"
        _run(scenario)


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)
