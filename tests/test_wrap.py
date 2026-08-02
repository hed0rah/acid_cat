"""acidcat wrap -- the step that turns a recovered region into a file.

`locate` finds headerless audio, `--analyze` infers its geometry, `carve` pulls
the bytes. Before this verb the chain stopped there: raw PCM is not something a
player will open, and the only way to hear a recovered region was the TUI's
in-memory audition. This closes it as a filter, so the whole recovery is a
pipeline rather than a detour through Python.
"""

import struct
import subprocess
import sys

import pytest

from acidcat.cli import main


def _pcm_le(values):
    return b"".join(struct.pack("<h", v) for v in values)


def _parse(wav):
    """(tag, channels, rate, bits, payload) from a minimal WAV."""
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    tag, ch, rate, _br, _ba, bits = struct.unpack_from("<HHIIHH", wav, 20)
    size = struct.unpack_from("<I", wav, 40)[0]
    return tag, ch, rate, bits, wav[44:44 + size]


def test_wraps_raw_pcm_into_a_playable_wav(tmp_path, capsys):
    raw = tmp_path / "r.pcm"
    raw.write_bytes(_pcm_le([0, 1000, -1000, 32767, -32768] * 20))
    out = tmp_path / "o.wav"
    assert main(["wrap", str(raw), "-o", str(out), "--rate", "22050"]) == 0
    tag, ch, rate, bits, payload = _parse(out.read_bytes())
    assert (tag, ch, rate, bits) == (1, 1, 22050, 16)
    assert payload == raw.read_bytes()


def test_big_endian_input_is_swapped_because_wav_is_little_endian(tmp_path):
    """The reason this needs a flag at all. `locate --analyze` reports
    big-endian geometry routinely -- it is normal in sampler and console
    formats -- and WAV cannot store it, so the bytes must be reordered."""
    samples = [0x0102, 0x0304, -2]
    raw = tmp_path / "r.pcm"
    raw.write_bytes(b"".join(struct.pack(">h", v) for v in samples))
    out = tmp_path / "o.wav"
    assert main(["wrap", str(raw), "-o", str(out), "--endian", "be"]) == 0
    assert _parse(out.read_bytes())[4] == b"".join(
        struct.pack("<h", v) for v in samples)


@pytest.mark.parametrize("bits", [8, 16, 24, 32, 64])
def test_every_width_round_trips(tmp_path, bits):
    step = bits // 8
    raw = tmp_path / "r.pcm"
    raw.write_bytes(bytes(range(256)) * step)
    out = tmp_path / "o.wav"
    assert main(["wrap", str(raw), "-o", str(out), "--bits", str(bits)]) == 0
    assert _parse(out.read_bytes())[3] == bits


def test_swap_is_its_own_inverse():
    """Swapping twice must return the original, or a be->le->be round trip
    through carve and wrap would silently corrupt samples."""
    from acidcat.commands.wrap import _swap
    for width in (16, 24, 32, 64):
        data = bytes(range(width // 8 * 8))
        assert _swap(_swap(data, width), width) == data


def test_partial_frame_is_dropped_and_reported(tmp_path, capsys):
    """A carved range rarely lands on a frame boundary. Truncating silently
    would put half a sample at the end of the file."""
    raw = tmp_path / "r.pcm"
    raw.write_bytes(b"\x01\x02\x03")                  # 1.5 frames at 16-bit
    out = tmp_path / "o.wav"
    assert main(["wrap", str(raw), "-o", str(out)]) == 0
    assert "dropped 1 trailing byte" in capsys.readouterr().err
    assert len(_parse(out.read_bytes())[4]) == 2


def test_float_requires_a_float_width(tmp_path, capsys):
    raw = tmp_path / "r.pcm"
    raw.write_bytes(bytes(64))
    assert main(["wrap", str(raw), "--bits", "16", "--float",
                 "-o", str(tmp_path / "o.wav")]) == 2
    assert "--float needs" in capsys.readouterr().err


def test_float_wav_is_tagged_as_float(tmp_path):
    raw = tmp_path / "r.pcm"
    raw.write_bytes(struct.pack("<4f", 0.0, 0.5, -0.5, 1.0))
    out = tmp_path / "o.wav"
    assert main(["wrap", str(raw), "-o", str(out), "--bits", "32",
                 "--float"]) == 0
    assert _parse(out.read_bytes())[0] == 3          # WAVE_FORMAT_IEEE_FLOAT


def test_absurd_rate_is_refused(tmp_path, capsys):
    """The rate lands in a u32 byte_rate field; a wild value there yields a
    header no player accepts, which is worse than an error."""
    raw = tmp_path / "r.pcm"
    raw.write_bytes(bytes(64))
    assert main(["wrap", str(raw), "--rate", "99999999",
                 "-o", str(tmp_path / "o.wav")]) == 2
    assert "--rate must be" in capsys.readouterr().err


def test_empty_input_is_an_error_not_an_empty_wav(tmp_path, capsys):
    raw = tmp_path / "r.pcm"
    raw.write_bytes(b"")
    assert main(["wrap", str(raw), "-o", str(tmp_path / "o.wav")]) == 1
    assert "no input bytes" in capsys.readouterr().err


def test_the_recovery_pipeline_composes_over_stdio(tmp_path):
    """The point of making this a filter: carve | wrap, no temp files."""
    payload = _pcm_le(list(range(-400, 400)))
    img = tmp_path / "disk.img"
    img.write_bytes(bytes(2048) + payload + bytes(512))
    env = {"PYTHONPATH": "src", "SystemRoot": "C:\\Windows", "PATH": ""}
    carved = subprocess.run(
        [sys.executable, "-m", "acidcat", "carve", str(img),
         "--offset", "2048", "--length", str(len(payload)), "-q"],
        capture_output=True, env=env)
    assert carved.returncode == 0, carved.stderr
    wrapped = subprocess.run(
        [sys.executable, "-m", "acidcat", "wrap", "--rate", "44100"],
        input=carved.stdout, capture_output=True, env=env)
    assert wrapped.returncode == 0, wrapped.stderr
    assert _parse(wrapped.stdout)[4] == payload


# ------------------------------------------- carve --batch --wrap (bulk QoL)

def _wav_bytes(n_frames=64, rate=22050):
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", n_frames * 2) + bytes(n_frames * 2))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_batch_wrap_makes_headerless_regions_playable(tmp_path, capsys):
    """The bulk case: recovering 18 blobs should not mean 18 shell invocations."""
    import json
    payload = _pcm_le(list(range(-500, 500)))
    img = tmp_path / "d.img"
    img.write_bytes(bytes(1024) + payload)
    recs = [{"offset": 1024, "length": len(payload), "kind": "blob",
             "format": None,
             "geometry": {"width": 16, "channels": 1, "endian": "le",
                          "float": False, "rate": None,
                          "rate_candidates": [44100]}}]
    src = tmp_path / "regions.json"
    src.write_text(json.dumps(recs))
    out = tmp_path / "out"
    assert main(["carve", str(img), "--batch", str(src), "--wrap",
                 "-o", str(out)]) == 0
    files = sorted(out.iterdir())
    assert len(files) == 1 and files[0].suffix == ".wav", files
    assert _parse(files[0].read_bytes())[4] == payload
    assert "wrapped as WAV" in capsys.readouterr().err


def test_batch_wrap_leaves_containers_alone(tmp_path):
    """A carved container already has a header. Wrapping it would produce a
    WAV inside a WAV, and the recovered file would no longer match the original."""
    import json
    original = _wav_bytes()
    img = tmp_path / "d.img"
    img.write_bytes(bytes(512) + original)
    src = tmp_path / "r.json"
    src.write_text(json.dumps([{"offset": 512, "length": len(original),
                                "kind": "container", "format": "wav"}]))
    out = tmp_path / "out"
    assert main(["carve", str(img), "--batch", str(src), "--wrap",
                 "-o", str(out)]) == 0
    got = sorted(out.iterdir())[0]
    assert got.read_bytes() == original, "a container was re-wrapped"


def test_batch_wrap_honours_big_endian_geometry(tmp_path):
    import json
    samples = [0x0102, 0x0304, -2, 7]
    payload = b"".join(struct.pack(">h", v) for v in samples)
    img = tmp_path / "d.img"
    img.write_bytes(payload)
    src = tmp_path / "r.json"
    src.write_text(json.dumps([{"offset": 0, "length": len(payload),
                                "kind": "blob", "format": None,
                                "geometry": {"width": 16, "channels": 1,
                                             "endian": "be", "rate": 44100}}]))
    out = tmp_path / "out"
    assert main(["carve", str(img), "--batch", str(src), "--wrap",
                 "-o", str(out)]) == 0
    got = sorted(out.iterdir())[0].read_bytes()
    assert _parse(got)[4] == b"".join(struct.pack("<h", v) for v in samples)


def test_batch_wrap_falls_back_to_raw_without_geometry(tmp_path):
    """No geometry means no defensible header, so write the bytes and say
    nothing rather than invent a sample format."""
    import json
    img = tmp_path / "d.img"
    img.write_bytes(bytes(range(256)))
    src = tmp_path / "r.json"
    src.write_text(json.dumps([{"offset": 0, "length": 256, "kind": "blob",
                                "format": None}]))
    out = tmp_path / "out"
    assert main(["carve", str(img), "--batch", str(src), "--wrap",
                 "-o", str(out)]) == 0
    assert sorted(out.iterdir())[0].suffix == ".raw"


def test_batch_without_wrap_is_unchanged(tmp_path):
    """The flag is opt-in; existing pipelines must behave exactly as before."""
    import json
    img = tmp_path / "d.img"
    img.write_bytes(bytes(range(256)))
    src = tmp_path / "r.json"
    src.write_text(json.dumps([{"offset": 0, "length": 256, "kind": "blob",
                                "format": None,
                                "geometry": {"width": 16, "channels": 1,
                                             "endian": "le", "rate": 44100}}]))
    out = tmp_path / "out"
    assert main(["carve", str(img), "--batch", str(src), "-o", str(out)]) == 0
    got = sorted(out.iterdir())[0]
    assert got.suffix == ".raw" and got.read_bytes() == bytes(range(256))


def test_locate_records_carry_geometry_to_carve():
    """Regression: _parse_records dropped `geometry`, so --wrap silently
    wrapped nothing even when locate --analyze had inferred it."""
    import json
    from acidcat.commands.carve import _parse_records
    recs = _parse_records(json.dumps([{"offset": 0, "end": 16, "length": 16,
                                       "kind": "blob", "format": None,
                                       "geometry": {"width": 16}}]))
    assert recs[0]["geometry"] == {"width": 16}


def test_hand_written_records_need_only_offset_and_length(tmp_path):
    """Regression: dict.get evaluates its default eagerly, so the old
    `r.get("length", r["end"] - r["offset"])` raised KeyError on any record
    carrying length but not end. locate always emits both, so this only bit
    someone scripting their own regions -- which is a supported use."""
    import json
    from acidcat.commands.carve import _parse_records
    recs = _parse_records(json.dumps([{"offset": 8, "length": 16}]))
    assert recs == [{"offset": 8, "length": 16, "kind": "region",
                     "format": None, "geometry": None}]


def test_a_record_with_neither_length_nor_end_is_a_clear_error(tmp_path):
    import json
    import pytest as _pytest
    from acidcat.commands.carve import _parse_records
    with _pytest.raises(ValueError, match="neither"):
        _parse_records(json.dumps([{"offset": 8}]))


def test_geometry_width_is_bits_not_bytes(tmp_path):
    """Regression on a unit confusion I shipped for one commit. audioscan's
    `width` is BITS throughout (analyze_geometry divides by 8 to get bytes), and
    a version of --wrap tried to guess the unit -- which would have rendered a
    genuine 8-bit region with a 64-bit header."""
    import json
    img = tmp_path / "d.img"
    img.write_bytes(bytes(range(256)))
    src = tmp_path / "r.json"
    src.write_text(json.dumps([{"offset": 0, "length": 256, "kind": "blob",
                                "format": None,
                                "geometry": {"width": 8, "channels": 1,
                                             "endian": None, "rate": 44100}}]))
    out = tmp_path / "out"
    assert main(["carve", str(img), "--batch", str(src), "--wrap",
                 "-o", str(out)]) == 0
    got = sorted(out.iterdir())[0]
    assert got.suffix == ".wav"
    assert _parse(got.read_bytes())[3] == 8, "8-bit geometry did not stay 8-bit"


def test_tsv_and_json_record_paths_agree(tmp_path):
    """`locate --analyze` emits geometry in both formats, so --wrap must work
    from either. The TSV parser used to ignore those columns, which made the
    two output formats silently non-interchangeable."""
    import json
    from acidcat.commands.carve import _parse_records
    tsv = _parse_records("0x00000000\t256\tblob\traw-pcm\t0.91\t16\t1\tbe\n")
    assert tsv[0]["geometry"] == {"width": 16, "channels": 1, "endian": "be"}
    js = _parse_records(json.dumps([{"offset": 0, "length": 256, "kind": "blob",
                                     "format": None,
                                     "geometry": {"width": 16, "channels": 1,
                                                  "endian": "be"}}]))
    assert tsv[0]["geometry"] == js[0]["geometry"]
