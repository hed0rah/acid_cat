"""The shared signal primitives (Phase-0 dedup of hand-rolled entropy + coherence)."""
import array
import math
import os

from acidcat.core.primitives.signal import (byte_counts, byte_entropy,
                                            entropy_from_counts, pcm_coherence)


def test_byte_counts():
    assert byte_counts(b"") == [0] * 256
    c = byte_counts(b"AAAB")
    assert c[ord("A")] == 3 and c[ord("B")] == 1 and sum(c) == 4


def test_byte_entropy_bounds():
    assert byte_entropy(b"") == 0.0
    assert byte_entropy(b"AAAAAAAA") == 0.0                 # single symbol -> 0 bits
    assert byte_entropy(bytes(range(256))) == 8.0           # uniform 256 -> exactly 8
    assert abs(byte_entropy(b"AB") - 1.0) < 1e-12           # two equal symbols -> 1 bit


def test_byte_entropy_matches_naive():
    def naive(blob):
        if not blob:
            return 0.0
        counts = [0] * 256
        for b in blob:
            counts[b] += 1
        n = len(blob)
        return -sum((c / n) * math.log2(c / n) for c in counts if c)
    for data in (b"hello world" * 7, os.urandom(1000), bytes(range(64)) * 3):
        assert abs(byte_entropy(data) - naive(data)) < 1e-12


def test_entropy_from_counts():
    assert entropy_from_counts([], 0) == 0.0
    assert entropy_from_counts([5, 5], 10) == 1.0           # two equal -> 1 bit
    assert entropy_from_counts([10, 0], 10) == 0.0          # certain -> 0 bits
    # binary form (the old lsb._bit_entropy): ones/total split
    ones, total = 3, 10
    got = entropy_from_counts([ones, total - ones], total)
    p = ones / total
    assert abs(got - (-p * math.log2(p) - (1 - p) * math.log2(1 - p))) < 1e-12


def _pcm(samples):
    return array.array("h", samples).tobytes()


def test_pcm_coherence():
    # too few samples -> zero autocorr, regardless of content
    assert pcm_coherence(_pcm([1000, -1000]), min_len=256) == (0.0, 0, 0.0)
    # a smooth ramp (adjacent samples close) is highly coherent
    ramp = _pcm(list(range(-8000, 8000, 4)) * 2)
    r, peak, rms = pcm_coherence(ramp, min_peak=100)
    assert r > 0.99 and peak >= 100 and rms > 0
    # peak gate: below min_peak -> autocorr 0, but peak/rms still reported honestly
    quiet = _pcm([5, -5] * 500)
    r2, peak2, _ = pcm_coherence(quiet, min_peak=1000)
    assert r2 == 0.0 and peak2 == 5
    # matches the old inline formula exactly on a real-ish signal
    sig = _pcm([int(500 * math.sin(i / 20)) for i in range(4000)])
    s = array.array("h"); s.frombytes(sig); w = s[:12000]
    m = sum(w) / len(w); var = sum((x - m) ** 2 for x in w) / len(w) or 1
    cov = sum((w[k] - m) * (w[k + 1] - m) for k in range(len(w) - 1)) / (len(w) - 1)
    assert abs(pcm_coherence(sig, min_peak=0)[0] - cov / var) < 1e-12


def test_entropy_matches_the_direct_definition():
    """entropy_from_counts moves the logarithm onto the integer counts so they
    can be tabled. It must still equal -sum(p*log2 p) to floating-point noise."""
    import math
    import random
    from acidcat.core.primitives.signal import entropy_from_counts

    def direct(counts, total):
        h = 0.0
        for c in counts:
            if c:
                p = c / total
                h -= p * math.log2(p)
        return h

    rng = random.Random(11)
    for n in (16, 256, 1024, 4096):
        counts = [0] * 256
        for _ in range(n):
            counts[rng.randrange(256)] += 1
        assert abs(entropy_from_counts(counts, n) - direct(counts, n)) < 1e-9


def test_entropy_edge_cases():
    from acidcat.core.primitives.signal import entropy_from_counts
    assert entropy_from_counts([0] * 256, 0) == 0.0        # no data
    assert entropy_from_counts([64] + [0] * 255, 64) == 0.0  # one symbol: no surprise
    assert abs(entropy_from_counts([4] * 256, 1024) - 8.0) < 1e-12  # uniform bytes


def test_log2_table_stays_capped():
    """Found by an adversarial audit: the log2 table grew to the largest TOTAL
    ever measured and lived forever in the module global -- one whole-blob call
    over 8 MB pinned 272 MB of floats for the life of the process, and
    anomalies.py hands this up to 64 MB of attacker-controlled bytes. Above the
    cap a count takes a direct math.log2; the answer must not change."""
    import math
    from acidcat.core.primitives import signal
    big = signal._LOG2_CAP * 4
    counts = [big // 2, big // 2] + [0] * 254
    h = signal.entropy_from_counts(counts, big)
    assert abs(h - 1.0) < 1e-9                       # two equal symbols: 1 bit
    assert len(signal._LOG2) <= signal._LOG2_CAP
    # mixed small and huge counts against the direct formula
    counts = [big, 3, 5, 7] + [0] * 252
    n = sum(counts)
    ref = -sum((c / n) * math.log2(c / n) for c in counts if c)
    assert abs(signal.entropy_from_counts(counts, n) - ref) < 1e-9
    assert len(signal._LOG2) <= signal._LOG2_CAP
