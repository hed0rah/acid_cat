"""One byte, one style, both columns -- and no new hex grids.

`_hex_rows` styled the ascii column independently of `cmap`, so a caller could
not put a single style on a byte. That one gap is why the hex editor carried a
hand-inlined copy of the whole row loop: it needed a cursor on both halves of a
row and there was no way to ask for one.

The count test is the part that matters long-term. There are already several
things that draw a hex grid, and every one of them is a place a future change
has to be made twice. The rule is that the overlay work adds a tag provider,
never a renderer.
"""

import pathlib
import re

import pytest

from rich.text import Text

from acidcat.tui_app.render import _hex_rows


def _spans_with(t, colour):
    """The (start, end) spans carrying a colour, read off the Text directly --
    rendering needs a Console and we only care about the styling."""
    return [(sp.start, sp.end) for sp in t.spans if colour.lower() in str(sp.style).lower()]


def test_the_cmap_reaches_the_ascii_column():
    """The three-line fix. Without it the ascii half ignores the caller."""
    t = Text()
    _hex_rows(t, 0, b"AAAA", "#111111", {2: "#ff0000"})
    hits = _spans_with(t, "#ff0000")
    # the byte appears twice on the row: once as hex, once as ascii
    assert len(hits) == 2, f"cmap reached {len(hits)} column(s), want 2: {hits}"
    # and they are in different halves of the row
    assert hits[0][0] < hits[1][0]


def test_an_unmapped_byte_keeps_the_printable_signal():
    """The cmap must not flatten the one thing the ascii column already says."""
    t = Text()
    _hex_rows(t, 0, b"A\x00", "#111111")
    plain = t.plain
    assert "A" in plain and "." in plain


def test_the_hex_editor_no_longer_carries_its_own_row_loop():
    """It was inlined only because of the gap above. If this fails, a second
    grid has grown back."""
    src = pathlib.Path("src/acidcat/tui_app/app.py").read_text(encoding="utf-8")
    body = src[src.index("def _render_hexedit"):]
    body = body[:body.index("\n    def ", 10)]
    assert "_hex_rows(" in body, "the editor stopped reusing the shared renderer"
    assert "for row in range(0, len(" not in body, "a row loop grew back"


def test_the_set_of_hex_grid_renderers_is_known():
    """A structural invariant, in the spirit of
    test_formats.py::test_walker_keys_are_known_formats.

    Each of these draws a 16-column hex grid. Adding a sixth means a future
    change has to be made in six places, and the overlay work is explicitly a
    tag provider rather than a renderer. If this fails, either fold the new one
    into an existing renderer or justify it here.
    """
    known = {
        "src/acidcat/commands/od.py",            # _raw_dump, the CLI grid
        "src/acidcat/tui_app/render.py",         # _hex_rows, the TUI grid
        "src/acidcat/core/probe.py",             # hexdump, pre-existing
        "src/acidcat/explorer.py",               # _byte_grid, HTML
    }
    # a hex grid is a loop that steps by a row width and formats %02x
    pat = re.compile(r"for \w+ in range\(0, len\([^)]*\), *(?:16|width|args\.width)\)")
    found = set()
    for p in pathlib.Path("src/acidcat").rglob("*.py"):
        txt = p.read_text(encoding="utf-8", errors="replace")
        if pat.search(txt) and ":02x" in txt:
            found.add(p.as_posix().split("acidcat/", 1)[-1].join(["src/acidcat/", ""])
                      if False else str(p).replace("\\", "/"))
    extra = found - known
    assert not extra, f"a new hex grid appeared in {sorted(extra)}"
