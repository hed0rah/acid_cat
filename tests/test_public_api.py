"""The public API is a promise, and 1.0 is when we start keeping it.

`acidcat.core.*` and `acidcat.commands.*` are documented as internal and free to
move. That is only honest if the package root actually exposes what a consumer
needs -- and it did not. The one tool built on acidcat ended up with two
hand-rolled container-magic tables because there was no public way to ask what a
byte string is.

That is the failure mode a missing export produces: duplicated tables, not a
broken import. An import audit cannot see it, which is why it survived.
"""

import struct

import pytest

import acidcat


def _wav(path, frames=400):
    pcm = b"".join(struct.pack("<h", (i * 137) % 20000 - 10000) for i in range(frames))
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


def test_everything_in_all_actually_resolves():
    """An __all__ entry that is not an attribute is a lie a star-import finds."""
    missing = [n for n in acidcat.__all__ if not hasattr(acidcat, n)]
    assert not missing, missing


@pytest.mark.parametrize("name", [
    "sniff", "sniff_bytes", "locate", "iter_pages", "decode_8svx", "play",
])
def test_the_capability_is_public(name):
    assert name in acidcat.__all__ and hasattr(acidcat, name)


def test_sniff_identifies_a_file_without_reaching_into_core(tmp_path):
    """The export that existed to be missed: identification."""
    p = _wav(tmp_path / "a.wav")
    assert acidcat.sniff(p) == "wav"


def test_sniff_bytes_identifies_a_header_with_no_file_at_all(tmp_path):
    """The form the duplicated magic tables were reimplementing."""
    head = open(_wav(tmp_path / "b.wav"), "rb").read(64)
    assert acidcat.sniff_bytes(head) == "wav"
    assert acidcat.sniff_bytes(b"OggS" + bytes(60)) == "ogg"
    assert acidcat.sniff_bytes(b"fLaC" + bytes(60)) == "flac"
    assert acidcat.sniff_bytes(b"\x00" * 64) is None


def test_locate_finds_a_container_planted_in_a_blob(tmp_path):
    """walk() reads a container; locate() finds the containers. The counterpart
    was internal, so the only ground-truth benchmark for it lived out of tree."""
    wav = open(_wav(tmp_path / "c.wav"), "rb").read()
    blob = bytes(5000) + wav + bytes(5000)
    hits = acidcat.locate(blob)
    assert hits, "planted a WAV and found nothing"
    assert any(h["offset"] <= 5000 < h.get("end", h["offset"] + h.get("length", 0))
               for h in hits), [h.get("offset") for h in hits]


def test_the_play_surface_is_whole():
    """A partial export is worse than none: the caller reaches back into
    util.play for the missing piece and the coupling returns."""
    for fn in ("play", "play_region", "play_bytes", "stop", "have_audio"):
        assert callable(getattr(acidcat.play, fn, None)), fn


def test_play_region_supports_non_blocking_with_a_stoppable_handle():
    """What an interactive caller needs: it must not block, and the thing it
    returns must be what stop() accepts."""
    import inspect
    sig = inspect.signature(acidcat.play.play_region)
    assert "block" in sig.parameters
    assert sig.parameters["block"].default is False
    assert "handle" in inspect.signature(acidcat.play.stop).parameters


def test_ogg_pages_are_enumerable(tmp_path):
    import os
    src = "data/test_formats/gs-16b-2c-44100hz.ogg"
    if not os.path.isfile(src):
        pytest.skip("no ogg fixture")
    pages = list(acidcat.iter_pages(open(src, "rb").read()))
    assert len(pages) > 1
    first = pages[0]
    for key in ("header_type", "serial", "data_off"):
        assert key in first, f"{key} missing; a multiplexer cannot work without it"


def test_import_acidcat_stays_lean():
    """The exports must not drag an optional dependency into the base install.
    `play` shells out to ffplay rather than importing an audio library, which is
    why it can be public at all."""
    import subprocess
    import sys
    code = ("import acidcat, sys; "
            "print([m for m in sys.modules "
            "if m.split('.')[0] in ('textual','rich','numpy','scipy','librosa','PIL')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "[]", out.stdout
