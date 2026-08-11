"""`carve --batch --wrap` must not silently leave regions headerless.

The header is built from the sample geometry in each record, and `locate --json`
only carries geometry when it ran with --analyze. Without it every region landed
as a .raw and the summary still said "carved N region(s)" -- the flag looked
honoured and nothing had been wrapped. A skipped job has to say so.
"""

import json
import struct

from acidcat.commands import carve


class _Args:
    def __init__(self, **kw):
        d = {"target": None, "offset": None, "length": None, "end": None,
             "trailing": False, "chunk": None, "raw": False, "output": None,
             "quiet": False, "at": None, "type": None, "count": 1,
             "endian": "be", "struct": None, "field": None, "encoding": None,
             "batch": None, "wrap": False, "rate": None}
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


_GEOMETRY = {"width": 16, "channels": 2, "endian": "le", "float": False}


def _blob(tmp_path):
    pcm = b"".join(struct.pack("<hh", i * 7 % 3000, -(i * 5 % 3000))
                   for i in range(2000))
    p = tmp_path / "disk.img"
    p.write_bytes(b"\x00" * 256 + pcm)
    return p, 256, len(pcm)


def _batch(tmp_path, records):
    p = tmp_path / "regions.json"
    p.write_text(json.dumps(records))
    return str(p)


def test_wrap_without_geometry_is_reported(tmp_path, capsys):
    img, off, length = _blob(tmp_path)
    recs = _batch(tmp_path, [{"offset": off, "length": length, "kind": "blob"}])
    out = tmp_path / "out"

    rc = carve.run(_Args(target=str(img), batch=recs, wrap=True,
                         rate=44100, output=str(out)))
    assert rc == 0

    names = sorted(p.name for p in out.iterdir())
    assert names == ["0000_0x00000100_blob.raw"]        # still headerless

    err = capsys.readouterr().err
    assert "left raw" in err and "--analyze" in err     # and it said so


def test_wrap_with_geometry_produces_a_wav(tmp_path, capsys):
    img, off, length = _blob(tmp_path)
    recs = _batch(tmp_path, [{"offset": off, "length": length, "kind": "blob",
                              "geometry": _GEOMETRY}])
    out = tmp_path / "out"

    assert carve.run(_Args(target=str(img), batch=recs, wrap=True,
                           rate=44100, output=str(out))) == 0

    written = sorted(out.iterdir())
    assert [p.name for p in written] == ["0000_0x00000100_blob.wav"]
    data = written[0].read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    # the geometry has to reach the header, not just trigger the wrap
    rate, chans, bits = struct.unpack("<I", data[24:28])[0], \
        struct.unpack("<H", data[22:24])[0], struct.unpack("<H", data[34:36])[0]
    assert (rate, chans, bits) == (44100, 2, 16)

    err = capsys.readouterr().err
    assert "wrapped as WAV at 44100 Hz" in err
    assert "left raw" not in err
