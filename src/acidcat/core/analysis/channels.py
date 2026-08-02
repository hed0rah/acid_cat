"""Does a stereo file carry stereo?

A file can declare two channels and hold one signal twice, which costs double
the disk and gives nothing back. It happens when a mono source is saved through
a stereo bus, and it is invisible in a waveform view because both halves look
correct. The check is cheap and exact for the common cases.

Three findings, in descending strength of claim:

    dual-mono    the channels are bit-identical -- certainly not stereo
    near-mono    correlation ~1.0 and negligible side energy; stereo in name
                 only (a mono source with dither or a trivial gain difference)
    stereo       genuine channel difference

Side energy is reported alongside correlation because the two answer different
questions: correlation asks whether the channels move together, side energy
asks whether the difference is loud enough to hear. A wide mix has both.
"""

_NEAR_MONO_CORR = 0.9995     # below this the channels genuinely differ
_NEAR_MONO_SIDE_DB = -60.0   # side energy this far under mid is inaudible


def analyze(channels, rate=None):
    """Channel-relationship finding for decoded PCM, or None if not stereo."""
    import numpy as np

    if len(channels) != 2:
        return None
    left, right = channels
    n = min(len(left), len(right))
    if n == 0:
        return None
    left, right = left[:n], right[:n]

    if np.array_equal(left, right):
        return {"check": "channels", "verdict": "dual-mono",
                "detail": ("both channels are bit-identical -- this is a mono "
                           "signal stored twice, at double the size"),
                "correlation": 1.0, "side_db": None}

    mid = (left + right) / 2.0
    side = (left - right) / 2.0
    mid_e = float(np.dot(mid, mid))
    side_e = float(np.dot(side, side))
    side_db = (10 * np.log10(side_e / mid_e) if mid_e > 0 and side_e > 0
               else float("-inf"))

    lz, rz = left - left.mean(), right - right.mean()
    denom = float(np.sqrt(np.dot(lz, lz) * np.dot(rz, rz)))
    corr = float(np.dot(lz, rz) / denom) if denom > 0 else 1.0

    if corr >= _NEAR_MONO_CORR and side_db <= _NEAR_MONO_SIDE_DB:
        verdict = "near-mono"
        detail = (f"channels correlate at {corr:.5f} with side energy "
                  f"{side_db:.1f} dB below mid -- stereo in name only")
    else:
        verdict = "stereo"
        detail = (f"channels differ (correlation {corr:.3f}, side energy "
                  f"{side_db:.1f} dB relative to mid)")

    return {"check": "channels", "verdict": verdict, "detail": detail,
            "correlation": round(corr, 6),
            "side_db": (None if side_db == float("-inf") else round(side_db, 1))}
