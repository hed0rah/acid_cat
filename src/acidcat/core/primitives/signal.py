"""Shannon-entropy primitives, shared across the forensic/scan modules.

Byte entropy (bits/byte, 0..8) is the workhorse "is this random/encrypted?"
measure; ~8 reads as ciphertext or compressed data, structured data well below.
Before this module it was reimplemented in audioscan, anomalies, triage, viz, and
lsb (as the 2-symbol bit variant) -- identical math, five copies.
"""

import array
import math


def pcm_coherence(pcm, min_peak=0, min_len=256):
    """(autocorr, peak, rms) over the first ~12000 16-bit mono samples of ``pcm``.

    autocorr is the mean-centered lag-1 correlation: ~1 is coherent audio, ~0 is
    noise. peak and rms are loudness gates -- below ``min_peak``, or fewer than
    ``min_len`` samples, the autocorr is 0.0. rms (sqrt of the variance) separates
    a sustained sample from a lone spike over near-silence, which peak alone is
    fooled by. Used by the container-agnostic ROM recovery (n64rip/snesrip) to
    score a decoded candidate; the ROM does not mark where samples are, so this is
    a detector threshold, not audio post-processing."""
    s = array.array("h")
    s.frombytes(pcm)
    if len(s) < min_len:
        return 0.0, 0, 0.0
    w = s[:12000]
    peak = max(abs(x) for x in w)
    if peak < min_peak:
        return 0.0, peak, 0.0
    m = sum(w) / len(w)
    var = sum((x - m) ** 2 for x in w) / len(w) or 1
    cov = sum((w[k] - m) * (w[k + 1] - m) for k in range(len(w) - 1)) / (len(w) - 1)
    return cov / var, peak, var ** 0.5


def byte_counts(data):
    """A 256-bin histogram of ``data``'s byte values."""
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    return counts


# log2 of small integers, grown on demand. The forensic scan calls
# entropy_from_counts once per window and every window is a 256-bin histogram,
# so the naive form spends millions of calls taking log2 of the same handful of
# small counts. Counts are integers, so they table exactly.
_LOG2 = [0.0, 0.0]                       # log2(0) unused, log2(1) = 0


def _log2_table(upto):
    while len(_LOG2) <= upto:
        _LOG2.append(math.log2(len(_LOG2)))
    return _LOG2


def entropy_from_counts(counts, total):
    """Shannon entropy (bits) of a symbol distribution given per-symbol ``counts``
    and their ``total``. Zero counts contribute nothing; ``total <= 0`` -> 0.0.

    Uses the identity H = log2(N) - (1/N) * sum(c * log2(c)), which moves the
    logarithm onto the integer counts so they can be looked up rather than
    recomputed. Same result as summing -p*log2(p), without the float logs.
    """
    if total <= 0:
        return 0.0
    tbl = _LOG2 if total < len(_LOG2) else _log2_table(total)
    acc = 0.0
    for c in counts:
        if c:
            acc += c * tbl[c]
    # Shannon entropy is non-negative; a certain distribution (one symbol with
    # every count) cancels to zero in exact arithmetic but lands a few ulp below
    # it here, since c*log2(c)/c does not round back to log2(c). Clamp rather
    # than hand a caller a negative "amount of information".
    h = tbl[total] - acc / total
    return h if h > 0.0 else 0.0


def byte_entropy(data):
    """Shannon entropy of raw bytes in bits/byte (0.0 .. 8.0). Empty -> 0.0."""
    n = len(data)
    if n == 0:
        return 0.0
    return entropy_from_counts(byte_counts(data), n)
