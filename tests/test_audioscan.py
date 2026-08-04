"""Tests for the statistical audio-blob detector (core/audioscan.py).

Deterministic: noise from a seeded PRNG, audio from a synthesized tone, so the
class-separation the multi-lag experiment showed is pinned as a regression."""

import math
import random
import struct

from acidcat.core.forensics import audioscan


def _noise(n, seed=1):
    r = random.Random(seed)
    return bytes(r.getrandbits(8) for _ in range(n))


def _tone(n, period=40, amp=60, phase=0.0):
    """A signed-8-bit sine rendered to unsigned bytes (a smooth, pitched blob)."""
    out = bytearray()
    for i in range(n):
        s = int(amp * math.sin(2 * math.pi * (i + phase) / period))
        out.append(s & 0xFF)
    return bytes(out)


def _code_like(n):
    """Low-entropy structured bytes: a small alphabet with local repetition,
    the way program text / opcodes cluster. Autocorr decays monotonically."""
    r = random.Random(7)
    alphabet = bytes(range(0x20, 0x40))                # 32 values, ASCII-ish
    out = bytearray()
    while len(out) < n:
        run = r.randint(1, 4)
        ch = alphabet[r.randrange(len(alphabet))]
        out.extend([ch] * run)
    return bytes(out[:n])


def test_noise_scores_zero():
    feat = audioscan.window_features(_noise(1024))
    assert feat["peak"] < 0.15                          # flat autocorrelation
    assert audioscan.audio_score(feat) == 0.0


def test_tone_scores_high():
    feat = audioscan.window_features(_tone(1024))
    assert feat["peak"] > 0.8                           # strongly correlated
    assert audioscan.audio_score(feat) > 0.7


def test_constant_is_not_a_blob():
    # a run of one byte has zero entropy -- ambiguous, not flagged as audio
    feat = audioscan.window_features(b"\x00" * 1024)
    assert feat["entropy"] < audioscan._ENTROPY_FLOOR
    assert audioscan.audio_score(feat) == 0.0


def test_code_scores_below_tone():
    code = audioscan.audio_score(audioscan.window_features(_code_like(1024)))
    tone = audioscan.audio_score(audioscan.window_features(_tone(1024)))
    assert code < tone
    assert code < audioscan.DEFAULT_MIN_SCORE           # rejected at the default gate


def test_features_include_distribution():
    feat = audioscan.window_features(_tone(1024))
    assert "printable" in feat and "hist_tv" in feat
    assert 0.0 <= feat["printable"] <= 1.0


def test_distribution_gate_rejects_printable_ramp():
    # a smooth (high-autocorr) but fully-printable ASCII ramp -- structurally
    # "waveform-like" yet obviously text. The distribution gate must veto it,
    # the calibrated defense against the code/text false positives.
    win = bytes(0x41 + (i % 30) for i in range(1024))   # sawtooth in 'A'.. range
    feat = audioscan.window_features(win)
    assert feat["printable"] > 0.95                     # all printable
    assert feat["peak"] > 0.4                            # and highly correlated
    assert audioscan.audio_score(feat) == 0.0           # ... still rejected


def test_buried_tone_is_located():
    # noise | TONE | noise -> exactly one region, overlapping the planted tone
    a0, a1 = 4096, 8192
    blob = _noise(a0, seed=2) + _tone(a1 - a0) + _noise(4096, seed=3)
    regions = audioscan.scan(blob)
    assert len(regions) == 1
    reg = regions[0]
    # the region lands on the tone (allow a window of slop at each edge)
    assert reg["start"] >= a0 - audioscan.DEFAULT_WINDOW
    assert reg["end"] <= a1 + audioscan.DEFAULT_WINDOW
    assert reg["confidence"] > 0.5
    # width now reports the reading that won, not a hardcoded 1: the scan tries
    # 8-bit and both 16-bit byte orders and keeps whichever scores best. An
    # 8-bit synthetic tone can legitimately also read as 16-bit, so assert the
    # value is one the detector actually supports rather than pinning the old
    # single-interpretation behaviour.
    assert reg["evidence"]["width"] in (1, 2)
    assert reg["evidence"]["view"] in ("8bit", "16bit-le", "16bit-be")


def test_two_tones_two_regions():
    gap = _noise(4096, seed=4)
    blob = gap + _tone(3072) + gap + _tone(3072, period=64) + gap
    regions = audioscan.scan(blob)
    assert len(regions) == 2
    assert regions[0]["end"] <= regions[1]["start"]


def test_scan_degrades_on_tiny_input():
    assert audioscan.scan(b"") == []
    assert audioscan.scan(b"\x01\x02\x03") == []        # shorter than a window


def _f32(n, period=40, amp=0.6):
    return b"".join(struct.pack("<f", amp * math.sin(2 * math.pi * i / period))
                    for i in range(n))


def test_float32_geometry():
    g = audioscan.analyze_geometry(_f32(2000))
    assert g["float"] is True and g["width"] == 32 and g["confidence"] > 0.7


def test_scan_finds_float_audio():
    # the fundamental blind spot: float PCM has high byte-entropy, so the integer
    # path misses it -- the float probe must catch it.
    blob = _noise(4096, 1) + _f32(3000) + _noise(4096, 2)
    regions = audioscan.scan(blob)
    assert len(regions) == 1 and regions[0]["confidence"] > 0.7


def test_random_is_not_float():
    g = audioscan.analyze_geometry(_noise(4096, 3))
    assert not g.get("float")                            # random bytes aren't float audio


def test_debug_tells():
    sil = audioscan.analyze_geometry(struct.pack("<2000h", *([0] * 2000)))
    assert sil["silence"] is True
    clip = audioscan.analyze_geometry(struct.pack("<2000h", *([32767, -32768] * 1000)))
    assert clip["clipping"] > 0.4
    dc = audioscan.analyze_geometry(struct.pack(
        "<2000h", *[10000 + int(2000 * math.sin(2 * math.pi * i / 40)) for i in range(2000)]))
    assert dc["dc_offset"] > 0.1


def test_hysteresis_bridges_short_gap():
    # tone | brief noise dip | tone -> ONE region, not two (real audio is dynamic)
    blob = _tone(4096) + _noise(1024, 9) + _tone(4096)
    regions = audioscan.scan(blob)
    assert len(regions) == 1
    assert regions[0]["start"] < 4096 and regions[0]["end"] > 4096 + 1024


def test_entropy_prefilter_skips_autocorr():
    # a near-maximal-entropy (random) window is pre-filtered: peak forced to 0
    feat = audioscan.window_features(_noise(1024, 3))
    assert feat["entropy"] > audioscan._ENTROPY_CEIL
    assert feat["peak"] == 0.0 and feat["structure"] == 0.0


def test_region_evidence_present():
    blob = _noise(2048, seed=5) + _tone(4096) + _noise(2048, seed=6)
    reg = audioscan.scan(blob)[0]
    ev = reg["evidence"]
    assert set(ev["autocorr"]) == set(audioscan.LAGS)
    assert 0.0 <= ev["entropy"] <= 8.0
    assert reg["windows"] >= 1


def test_silence_does_not_outrank_real_audio():
    """A near-constant run correlates perfectly, so before the liveness term it
    scored 1.0 and pushed actual content down the list -- the top hits on a real
    proprietary sample bank were all silence. Flat data is weak evidence (it is
    equally consistent with padding or a sparse hole), so it must be damped
    below genuine signal while still being reported in aggressive mode."""
    import math
    from acidcat.core.forensics.audioscan import window_features, audio_score

    # near-silence as it really occurs: 16-bit LE samples dithering within a few
    # LSBs of zero. A perfectly constant run is already caught by the entropy
    # floor; this is the case that slipped past it, matching what a real
    # proprietary bank contained (entropy ~2.8, ~13 distinct byte values).
    import random
    rng = random.Random(7)
    v, buf = 0, bytearray()
    for _ in range(768):
        v = max(-6, min(6, v + rng.choice((-1, 0, 1))))
        buf += (v & 0xFFFF).to_bytes(2, "little")
    silence = bytes(buf)
    # a smooth but live waveform: a sine sampled to 8-bit
    live = bytes((int(64 * math.sin(i / 8.0)) + 128) & 0xFF for i in range(1024))

    s_silence = audio_score(window_features(silence))
    s_live = audio_score(window_features(live))
    assert s_live > s_silence, (
        f"silence ({s_silence:.3f}) must not outrank real audio ({s_live:.3f})")
    # damped, not rejected outright -- aggressive mode still needs to see it
    assert s_silence > 0.0, "flat regions should still be reported, just ranked low"


def test_liveness_leaves_loud_audio_untouched():
    """The damping must not cost recall on ordinary content."""
    import math
    from acidcat.core.forensics.audioscan import window_features, audio_score
    loud = bytes((int(100 * math.sin(i / 5.0)) + 128) & 0xFF for i in range(1024))
    feat = window_features(loud)
    assert feat["spread"] > 16.0, "a loud waveform should clear the liveness ramp"


def test_autocorr_lags_matches_the_per_lag_form():
    """The scan computes every lag in one pass, sharing the centred deviations.
    It must agree with the straightforward per-lag computation exactly -- this is
    a speed change, not a behaviour change."""
    import random
    from acidcat.core.forensics.audioscan import _autocorr, _autocorr_lags, LAGS
    rng = random.Random(3)
    samples = [rng.randint(-128, 127) for _ in range(1024)]
    mean = sum(samples) / len(samples)
    den = sum((s - mean) ** 2 for s in samples)
    one_by_one = {L: _autocorr(samples, mean, den, L) for L in LAGS}
    together = _autocorr_lags(samples, mean, den, LAGS)
    for L in LAGS:
        assert abs(one_by_one[L] - together[L]) < 1e-12, f"lag {L} diverged"


def test_autocorr_lags_handles_a_flat_window():
    """Zero variance must not divide by zero."""
    from acidcat.core.forensics.audioscan import _autocorr_lags, LAGS
    flat = [7] * 256
    out = _autocorr_lags(flat, 7.0, 0.0, LAGS)
    assert all(out[L] == 0.0 for L in LAGS)
