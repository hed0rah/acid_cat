"""Tests for core/primitives/wavio.py -- the shared WAV emitter."""

import io
import struct
import wave

from acidcat.core.primitives.wavio import pcm_wav


def _read_back(buf):
    with wave.open(io.BytesIO(buf), "rb") as w:
        return (w.getnchannels(), w.getsampwidth(), w.getframerate(),
                w.readframes(w.getnframes()))


def test_roundtrip_mono():
    frames = b"".join(struct.pack("<h", (i % 100) * 256) for i in range(500))
    ch, sw, rate, data = _read_back(pcm_wav(frames, 8000))
    assert (ch, sw, rate) == (1, 2, 8000)
    assert data == frames


def test_roundtrip_stereo():
    frames = b"\x01\x02\x03\x04" * 300
    ch, sw, rate, data = _read_back(pcm_wav(frames, 44100, channels=2))
    assert (ch, sw, rate) == (2, 2, 44100)
    assert data == frames


def test_empty_frames():
    ch, sw, rate, data = _read_back(pcm_wav(b"", 22050))
    assert (ch, sw, rate) == (1, 2, 22050)
    assert data == b""


def test_matches_inline_wave_open():
    """pcm_wav must be byte-identical to the hand-rolled wave.open pattern it
    replaced across svx/samples/convert/cdxa/tui_app."""
    def inline(frames, rate, channels, sampwidth):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(sampwidth)
            w.setframerate(rate)
            w.writeframes(frames)
        return buf.getvalue()

    frames = b"\x11\x22" * 257  # odd frame count
    assert pcm_wav(frames, 8363, 1, 2) == inline(frames, 8363, 1, 2)
