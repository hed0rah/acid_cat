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
