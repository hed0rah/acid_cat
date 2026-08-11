"""WAV emission -- the one shared body behind every ``wave.open`` call site.

Callers own their own default-rate policy and pass the final rate in; this only
packs frames into canonical PCM WAV bytes. Formats that build a RIFF header by
hand (float / arbitrary bit depth, e.g. core/ncw.py) do not go through here.
"""

import io
import wave


def pcm_wav(frames, rate, channels=1, sampwidth=2):
    """Return WAV file bytes for interleaved PCM ``frames`` at ``rate`` Hz."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()
