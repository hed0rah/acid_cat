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
