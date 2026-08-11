"""Every bound key does something, and says so when it cannot.

These are the checks that would have caught the last three rounds of TUI bugs
before they were found by hand. The pattern each time was the same: an action
existed, a test called it directly and passed, and the *key* never reached it --
or reached it in a state where it silently did nothing.

So this drives real key presses through the real app and asserts on observable
state, never on the action methods.
"""

import asyncio
import os
import shutil

import pytest

from acidcat.tui_app.app import AcidcatTUI


def _run(coro_factory):
    asyncio.run(coro_factory())


@pytest.fixture
def wav(tmp_path):
    src = "data/test_formats/generated/src.wav"
    if not os.path.isfile(src):
        pytest.skip("generated wav corpus absent")
    p = tmp_path / "t.wav"
    shutil.copyfile(src, p)
    return str(p)


# keys that only make sense mid-scan, or that end the session
_EXEMPT = {"q", "escape", "space", "enter"}


def _bound_keys():
    keys = []
    for b in AcidcatTUI.BINDINGS:
        k = b[0] if isinstance(b, tuple) else b.key
        if k not in _EXEMPT:
            keys.append(k)
    return keys


@pytest.fixture(scope="module")
def keysweep(request):
    """Press every bound key once, in a fresh app each time, and record what it
    did. Module-scoped because it is the expensive part of this file -- one app
    per key rather than one per key per assertion.
    """
    src = "data/test_formats/generated/src.wav"
    if not os.path.isfile(src):
        pytest.skip("generated wav corpus absent")
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.wav")
    shutil.copyfile(src, path)

    async def sweep():
        out = {}
        for key in _bound_keys():
            app = AcidcatTUI(path)
            async with app.run_test(size=(140, 44)) as pilot:
                await pilot.pause()
                await pilot.press("down")          # land on a real chunk
                await pilot.pause()
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                before = _snapshot(app)
                await pilot.press(key)
                await pilot.pause()
                changed = _snapshot(app) != before
                opened = len(app.screen_stack) > 1
                escaped = None
                if opened:
                    await pilot.press("escape")
                    await pilot.pause()
                    escaped = len(app.screen_stack) == 1
                out[key] = {"changed": changed, "spoke": bool(notes),
                            "modal": opened, "escaped": escaped}
        return out

    return asyncio.run(sweep())


def test_no_bound_key_is_silently_inert(keysweep):
    """A key that changes nothing and says nothing is indistinguishable from a
    broken build. That is exactly how `tab` shipped unreachable: Textual's
    focus_next consumed it, every test passed because they called the action
    directly, and pressing it did nothing at all."""
    inert = [k for k, r in keysweep.items() if not r["changed"] and not r["spoke"]]
    assert not inert, f"keys that did nothing and said nothing: {inert}"


def _expanded(tree):
    """How much of the tree is unfolded -- what `a` and `c` change, and the one
    piece of state a naive snapshot misses, which makes those two keys look
    inert when they are not."""
    n = 0
    stack = [tree.root]
    while stack:
        node = stack.pop()
        if getattr(node, "is_expanded", False):
            n += 1
            stack.extend(node.children)
    return n


def _snapshot(app):
    bar = app.query_one("#editbar")
    tree = app.query_one("#tree")
    return (
        len(app.screen_stack),
        app._view,
        app._zoom,
        getattr(app.focused, "id", None),
        app._hexedit is not None,
        app._edit_target is not None,
        app._prompt,
        app.dirty,
        tree.cursor_line,
        _expanded(tree),
        "hidden" not in bar.classes,
    )


def test_every_modal_closes_on_escape(keysweep):
    """A modal you cannot leave is a hang. It also silently disables every
    single-letter binding underneath it (check_action), so a stuck modal reads
    as "the whole app stopped responding to keys" -- which is exactly how a
    working `z` can look broken."""
    stuck = [k for k, r in keysweep.items() if r["modal"] and not r["escaped"]]
    assert not stuck, f"modals that escape does not close: {stuck}"


def test_zoom_works_from_either_pane_and_toggles_back(wav):
    """z is the key the whole layout depends on, and it is focus-sensitive --
    which makes it exactly the kind of thing that breaks quietly."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            start = app.query_one("#tree").region.width

            await pilot.press("z")                  # tree is focused
            await pilot.pause()
            assert app._zoom == "zoom-tree"
            assert app.query_one("#tree").region.width > start
            await pilot.press("z")
            await pilot.pause()
            assert app._zoom is None
            assert app.query_one("#tree").region.width == start

            await pilot.press("tab")                # hex pane
            await pilot.press("z")
            await pilot.pause()
            assert app._zoom == "zoom-hex"
            assert app.query_one("#hexwrap").region.width > start
            await pilot.press("z")
            await pilot.pause()
            assert app._zoom is None
    _run(scenario)


def test_a_zoomed_pane_actually_fills_the_screen(wav):
    """Zoom that leaves the other column occupying space is not zoom."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.press("z")
            await pilot.pause()
            assert app.query_one("#right").region.width == 0
            assert app.query_one("#left").region.width == 140
            await pilot.press("z")
            await pilot.press("tab")
            await pilot.press("z")
            await pilot.pause()
            assert app.query_one("#left").region.width == 0
            assert app.query_one("#right").region.width == 140
    _run(scenario)


def test_the_footer_only_advertises_keys_that_work(keysweep):
    """The footer is the contract. A key shown there that does nothing is
    worse than one that is not shown at all."""
    shown = []
    for b in AcidcatTUI.BINDINGS:
        if isinstance(b, tuple):
            shown.append(b[0])
        elif getattr(b, "show", True):
            shown.append(b.key)
    broken = [k for k in shown
              if k in keysweep
              and not keysweep[k]["changed"] and not keysweep[k]["spoke"]]
    assert not broken, f"footer advertises inert keys: {broken}"


# ── mounting must not scale with a hostile chunk count ─────────────

def _null_tailed_wav(path, tail_bytes):
    """A valid WAV with a run of nulls appended.

    The deep walker reads each 8-byte run of zeros as a zero-size chunk, so
    this is the cheapest way to make a file with an enormous chunk count. It is
    not contrived: a truncated or zero-padded file from a failed transfer looks
    exactly like this, and opening damaged files is the tool's purpose.
    """
    import struct
    pcm = b"\x00\x01" * 200
    body = (b"WAVE" + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body
                     + bytes(tail_bytes))
    return path


def test_a_huge_chunk_count_does_not_freeze_the_mount(tmp_path):
    """_ROW_CAP bounded rows WITHIN a chunk; nothing bounded the chunks.

    One Tree widget per chunk, built synchronously in on_mount: 65,538 chunks
    took 11.5 s, 262,146 took 46 s with nothing painted -- so `q` was not
    available -- and 8.5 million never finished. `inspect` renders the same
    file in 3.4 s, which is what makes this the TUI's bug and not the walker's.
    """
    import time
    from acidcat.tui_app.app import AcidcatTUI
    p = _null_tailed_wav(tmp_path / "tail.wav", 2 * 1024 * 1024)

    async def go():
        app = AcidcatTUI(str(p))
        t0 = time.perf_counter()
        async with app.run_test(size=(120, 40)) as pilot:
            elapsed = time.perf_counter() - t0
            nodes = len(app.query_one("#tree").root.children)
            await pilot.press("q")
        return elapsed, nodes

    elapsed, nodes = asyncio.run(go())
    assert elapsed < 10.0, f"mount took {elapsed:.1f}s on a 2 MB file"
    assert nodes <= 2001, f"{nodes} top-level nodes; the chunk cap is not applied"


def test_the_hidden_chunks_are_counted_and_reachable(tmp_path):
    """A silently shortened tree makes a truncated file look complete, which is
    worse than the freeze. The cap has to name what it hid, and `+` must reach
    past it -- the same contract the row cap already honours."""
    from acidcat.tui_app.app import AcidcatTUI
    p = _null_tailed_wav(tmp_path / "tail.wav", 512 * 1024)

    async def go():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 40)) as pilot:
            tree = app.query_one("#tree")
            labels = [str(c.label) for c in tree.root.children]
            named = any("more chunks" in x for x in labels)
            before = len(tree.root.children)
            hits = [c for c in tree.root.children if "more chunks" in str(c.label)]
            if hits:
                tree.move_cursor(hits[0])
                app.action_more_rows()
                await pilot.pause()
            after = len(app.query_one("#tree").root.children)
            await pilot.press("q")
        return named, before, after

    named, before, after = asyncio.run(go())
    assert named, "the hidden chunks are not named, so the tree looks complete"
    assert after > before, f"+ did not extend the chunk budget ({before} -> {after})"
