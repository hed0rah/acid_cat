"""The triage verdict: single file, container, damaged, or not audio.

Answers the three questions asked before any expensive analysis, and names the
verb that follows. The costs it is allowed to pay were measured: magic ~0.08 ms,
embedded-container sweep ~76 ms on 32 MB. The statistical audio scan (~13 s on
the same file) is 174x the sweep and is never run here -- when it is the right
next step, that is *reported*.
"""

import os
import struct

import pytest

from acidcat.core.forensics import classify as C


def _wav(n_frames=64):
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
            + b"data" + struct.pack("<I", n_frames * 2) + bytes(n_frames * 2))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_known_format_is_single_and_routes_to_inspect(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    v = C.classify(str(p))
    assert v["shape"] == C.SINGLE
    assert v["format"] == "wav"
    assert v["next"] == "inspect"


def test_image_with_embedded_files_is_a_container(tmp_path):
    """Case (b): a bigger thing holding files. Found by the signature sweep,
    which is cheap enough to ask of anything."""
    p = tmp_path / "disk.img"
    p.write_bytes(bytes(4096) + _wav(2000) + bytes(512) + _wav(2000) + bytes(256))
    v = C.classify(str(p))
    assert v["shape"] == C.CONTAINER
    assert v["next"] == "locate"
    assert v["evidence"]["embedded_containers"] >= 2


def test_damaged_header_is_damaged_and_routes_to_resync(tmp_path):
    """Case (c): the magic is gone so nothing dispatches, but the chunk grid is
    still on disk. The verdict must say so and point at the verb that gets it."""
    p = tmp_path / "smashed.wav"
    p.write_bytes(b"\x00\x00\x00\x00" + _wav(2000)[4:])
    v = C.classify(str(p))
    assert v["shape"] == C.DAMAGED
    assert v["next"] == "inspect --resync"
    assert v["evidence"]["resync_chunks"] >= 2


def test_headless_payload_is_opaque_and_routes_to_locate(tmp_path):
    """No magic, no embedded containers, no recoverable grid -- audio may still
    be in there, but only the expensive statistical pass can find it, so the
    verdict names that rather than running it."""
    p = tmp_path / "payload.bin"
    p.write_bytes(_wav(20000)[4096:])
    v = C.classify(str(p))
    assert v["shape"] == C.OPAQUE
    assert v["next"] == "locate"


def test_non_audio_is_named_not_called_unrecognized(tmp_path):
    """1.9% of a real 3,229-file library was this: documents and art sitting in
    sample packs. Naming them is more useful than "unrecognized"."""
    for magic, name, expect in (
            (b"%PDF-1.7\n", "readme.pdf", "PDF"),
            (b"\x89PNG\r\n\x1a\n", "art.png", "PNG"),
            (b"\xff\xd8\xff\xe0", "cover.jpg", "JPEG"),
            (b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X", "._loop.wav",
             "AppleDouble")):
        p = tmp_path / name
        p.write_bytes(magic + b"\x00" * 512)
        v = C.classify(str(p))
        assert v["shape"] == C.FOREIGN, f"{name} -> {v['shape']}"
        assert expect.lower() in v["detail"].lower()
        assert v["next"] is None, "nothing to run on a non-audio file"


def test_compressed_wrappers_are_containers_not_foreign(tmp_path):
    """An Ableton Live Pack is a gzip stream; calling it 'not audio' would be
    wrong. Compressed wrappers may hold exactly what the user is looking for."""
    for magic, name in ((b"PK\x03\x04", "pack.alp"), (b"\x1f\x8b\x08", "lib.gz")):
        p = tmp_path / name
        p.write_bytes(magic + b"\x00" * 256)
        v = C.classify(str(p))
        assert v["shape"] == C.CONTAINER, f"{name} -> {v['shape']}"
        assert v["next"] == "extract"


def test_unknown_but_chunked_is_readable(tmp_path):
    """Plenty of 'unsupported' formats are IFF under another name -- a Reason
    .sxt is FORM/CAT/DESC and walks today with no format-specific walker."""
    body = b"CAT " + struct.pack(">I", 32) + bytes(32)
    body += b"DESC" + struct.pack(">I", 16) + bytes(16)
    p = tmp_path / "patch.sxt"
    p.write_bytes(b"FORM" + struct.pack(">I", len(body) + 4) + b"PTCH" + body)
    v = C.classify(str(p))
    assert v["shape"] in (C.CHUNKED, C.SINGLE)
    assert v["next"] == "inspect"


def test_empty_file(tmp_path):
    p = tmp_path / "nothing.bin"
    p.write_bytes(b"")
    assert C.classify(str(p))["shape"] == C.EMPTY


def test_shallow_skips_the_expensive_checks(tmp_path):
    """--shallow is for sorting a big tree, where a per-file sweep would
    dominate. It must not reach the container sweep."""
    p = tmp_path / "disk.img"
    p.write_bytes(bytes(4096) + _wav(2000))
    v = C.classify(str(p), deep=False)
    assert "embedded_containers" not in v["evidence"]


def test_cli_reports_and_routes(tmp_path, capsys):
    from acidcat.cli import main
    good = tmp_path / "a.wav"
    good.write_bytes(_wav())
    damaged = tmp_path / "smashed.wav"
    damaged.write_bytes(b"\x00\x00\x00\x00" + _wav(2000)[4:])
    rc = main(["classify", str(good), str(damaged), "--color", "never"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "single" in out and "damaged" in out
    assert "inspect --resync" in out, "no next step offered for the damaged file"


def test_cli_json_is_machine_readable(tmp_path, capsys):
    import json
    from acidcat.cli import main
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    main(["classify", str(p), "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["shape"] == "single" and rows[0]["format"] == "wav"


def test_named_but_unwalked_beats_opaque(tmp_path):
    """A file we can identify deserves its name. Calling a Reaktor ensemble
    "opaque" sends the user to a statistical scan when the honest answer is
    that the gap is ours, not the file's."""
    for magic, name, expect in (
            (b"NRKT\x00\x01\x00\x00", "8-Pole_Filter.ism", "Reaktor"),
            (b"MalC\x00\x00\x00\x00", "bank.glo", "Absynth"),
            (b"malc\x00\x00\x00\x00", "old.glo", "Absynth")):
        p = tmp_path / name
        p.write_bytes(magic + b"\x00" * 512)
        v = C.classify(str(p))
        assert v["shape"] == C.UNWALKED, f"{name} -> {v['shape']}"
        assert expect.lower() in v["detail"].lower()
        assert v["next"] == "od", "bytes are still inspectable"


def test_xml_backed_presets_are_named_as_text(tmp_path):
    """BFD3 grooves and palettes are plain XML -- the user can just read them,
    so neither "opaque" nor an acidcat verb is the right answer."""
    for body, name in ((b"<root>\r\n  <BFD2Groove name=\"g\"/>\r\n</root>",
                        "fill.bfd3grv"),
                       (b"<?xml version=\"1.0\"?><patch/>", "a.preset")):
        p = tmp_path / name
        p.write_bytes(body)
        v = C.classify(str(p))
        assert v["shape"] == C.UNWALKED
        assert "xml" in v["detail"].lower()
        assert v["next"] is None


def test_unwalked_does_not_shadow_a_real_walker(tmp_path):
    """The named table is checked after sniff, so a format we actually walk
    keeps its walker. Guards against a magic collision quietly downgrading a
    supported format."""
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    assert C.classify(str(p))["shape"] == C.SINGLE


def test_container_found_beyond_a_read_cap(tmp_path, monkeypatch):
    """Regression: a read cap made this lie. A large image with a WAV past the
    cap reported "no embedded containers" -- a confident wrong answer to the
    exact question the module exists to answer. The whole file is mapped now.

    Forced onto the mmap path with a tiny threshold so the test stays small."""
    monkeypatch.setattr(C, "_MMAP_ABOVE", 0)
    p = tmp_path / "disk.img"
    p.write_bytes(bytes(3 * 1024 * 1024) + _wav(4000))
    v = C.classify(str(p))
    assert v["shape"] == C.CONTAINER
    assert v["evidence"]["first_at"] == 3 * 1024 * 1024


def test_mapped_file_is_released(tmp_path, monkeypatch):
    """A leaked mmap keeps a Windows file locked, which would break any caller
    that classifies a file and then moves or deletes it."""
    monkeypatch.setattr(C, "_MMAP_ABOVE", 0)
    p = tmp_path / "scratch.bin"
    p.write_bytes(bytes(1024 * 1024) + b"\x00" * 16)
    C.classify(str(p))
    os.remove(str(p))                    # raises PermissionError if still mapped
    assert not p.exists()


def test_mmap_and_plain_read_agree(tmp_path, monkeypatch):
    """The two backing stores must not change the verdict."""
    p = tmp_path / "img.bin"
    p.write_bytes(bytes(2048) + _wav(3000) + bytes(999))
    monkeypatch.setattr(C, "_MMAP_ABOVE", 1 << 40)      # force plain read
    plain = C.classify(str(p))
    monkeypatch.setattr(C, "_MMAP_ABOVE", 0)            # force mmap
    mapped = C.classify(str(p))
    assert plain == mapped
