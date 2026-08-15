"""Looking at a chunk and acting on it are different byte ranges.

A RIFF chunk's `offset` points at its four-byte tag, `size` counts only the
payload, and the contents start eight bytes later. The tree used to give a chunk
node the range `(offset, size)`, which is neither: it began on the tag and ended
eight bytes short of the data. Nothing downstream compensated, so on a plain WAV:

    node range     0x000024..0x0007f4
    first bytes    b'data\\xd0\\x07\\x00\\x00AAAA'   <- tag and length, as audio
    real payload   0x00002c..0x0007fc

`p` fed those eight header bytes into the PCM stream and stopped eight bytes
early; `y` yanked them; the region-scoped graphs measured them. The proof it was
never intended: fields inside that same chunk are placed at `offset + 8`, so a
chunk and its own fields disagreed by eight bytes within one file.

Two ranges, because there are two questions:

    extent   what the hex pane shows -- you are inspecting the chunk, and its
             header is part of the chunk
    payload  what play, yank, carve and a recursive walk consume -- the
             contents, never the header
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
    n = 2000
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", n) + b"\x41" * n)
    p = tmp_path / "plain.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p), p.read_bytes(), n


def _data_node(app):
    for node in app.query_one("#tree").root.children:
        if "data" in app._node_name(node):
            return node
    raise AssertionError("no data chunk in the tree")


class TestTheTwoRanges:
    def test_the_hex_pane_shows_the_whole_chunk(self, wav):
        path, raw, n = wav

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                off, length, _a = app._meta(_data_node(app))
                tag = raw.index(b"data")
                assert off == tag, "the extent should start at the tag"
                assert off + length == tag + 8 + n, (
                    "the extent should end where the chunk ends")
        _run(scenario)

    def test_actions_get_the_payload_and_only_the_payload(self, wav):
        path, raw, n = wav

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                app._cur_node = _data_node(app)
                off, length = app._act_range()
                tag = raw.index(b"data")
                assert (off, length) == (tag + 8, n)
                assert raw[off:off + 4] == b"AAAA", (
                    f"an action would start on {raw[off:off + 4]!r}")
                assert off + length == len(raw), "it stops short of the end"
        _run(scenario)

    def test_the_header_is_never_handed_to_a_consumer(self, wav):
        """The specific old symptom: the four ASCII bytes of the tag arriving
        at the audio player as though they were samples."""
        path, raw, _n = wav

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                app._cur_node = _data_node(app)
                off, _length = app._act_range()
                assert raw[off:off + 4] != b"data"
        _run(scenario)

    def test_a_field_acts_on_itself(self, wav):
        """A field has no header, so its two ranges are the same range. The
        fallback has to give the node's own extent rather than nothing."""
        path, _raw, _n = wav

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                chunk = [c for c in app.query_one("#tree").root.children
                         if c.allow_expand][0]
                chunk.expand()
                await pilot.pause(0.2)
                field = chunk.children[0]
                app._cur_node = field
                off, length, _a = app._meta(field)
                assert app._act_range() == (off, length)
        _run(scenario)

    def test_nothing_selected_is_not_a_crash(self, wav):
        path, _raw, _n = wav

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                app._cur_node = None
                app._act_range()                     # must not raise
        _run(scenario)
