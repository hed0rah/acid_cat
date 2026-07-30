"""Container-agnostic SNES BRR sample recovery.

SNES games store BRR samples in game-specific ways -- a sample directory in ARAM
uploaded from ROM, pointer tables that differ per engine -- but the BRR blocks
themselves sit contiguously in the ROM, and because BRR carries no codebook a
sample is entirely self-describing. So we recover without any container:

  1. walk the ROM block by block; a run of valid blocks (shift <= 12) terminated
     by an end-flag block is a candidate sample;
  2. decode it with core/brr.py and keep the ones that come out *loud and
     coherent* (peak AND sample-to-sample autocorrelation -- autocorrelation
     alone is fooled by the silence/loop pads that pack a sample table);
  3. resync a byte at a time whenever a run doesn't terminate cleanly.

"PhotoRec for audio" on a SNES ROM: it rescues the samples, not the instrument
table or their tuning/loop metadata (that lives in the ARAM directory we skip).

    from acidcat.core.extract import snesrip
    for s in snesrip.recover(rom_bytes):
        ...  # s["pcm"] is 16-bit mono PCM; samples.py wraps it to WAV
"""



from acidcat.core.codecs import brr
from acidcat.core.primitives import signal

# a 512-byte copier (SMC) header offsets everything; strip it before scanning.
_COPIER = 512


def _strip_copier(data):
    return data[_COPIER:] if len(data) % 1024 == _COPIER else data


def _coherence(pcm, min_peak):
    # (autocorr, peak, rms): mean-centered lag-1 autocorr + loudness gates. rms
    # separates a sustained sample from a lone spike over near-silence. Shared with
    # n64rip via primitives.signal (BRR samples can be short, so no length floor).
    return signal.pcm_coherence(pcm, min_peak)


def _run_length(data, start, max_blocks):
    """Blocks in the valid-block run at `start`, and whether it ended on an end
    flag. Stops at an invalid shift, an end flag, EOF, or the block cap."""
    n = len(data)
    pos = start
    nb = 0
    while pos + brr.BLOCK <= n and nb < max_blocks:
        header = data[pos]
        if not brr.block_valid(header):
            return nb, False
        nb += 1
        pos += brr.BLOCK
        if header & 0x01:                              # end flag -> clean sample end
            return nb, True
    return nb, False


def recover(data, *, min_peak=600, min_coherence=0.9, min_rms=500, min_blocks=16,
            max_blocks=0x4000, max_samples=256):
    """Recover coherent BRR samples from a SNES ROM. Yields dicts with pcm (16-bit
    mono bytes), offset (into the stripped ROM), coherence, peak, rms, and blocks.

    Any structurally valid, end-flag-terminated run advances the cursor past the
    whole sample (staying block-aligned through a sample table); only the loud and
    coherent ones are yielded -- loud measured by rms (sustained energy), not just
    peak, so a lone spike over near-silence does not read as a sample. A run that
    never terminates cleanly resyncs a byte."""
    data = _strip_copier(data)
    n = len(data)
    yielded = 0
    pos = 0
    while pos + brr.BLOCK <= n:
        if not brr.block_valid(data[pos]):
            pos += 1
            continue
        nb, clean = _run_length(data, pos, max_blocks)
        if not clean:
            # a long run of valid headers that never ends on a flag is not a sample
            # (samples are end-flag-terminated), so skip the whole scanned span
            # instead of re-scanning it byte by byte. 0x00 padding is a valid shift-0
            # header with no end flag, so byte-at-a-time resync there is
            # O(n*max_blocks) and hangs; only short junk (a possible misaligned
            # sample-table start a few bytes ahead) is worth creeping toward.
            pos += nb * brr.BLOCK if nb >= min_blocks else 1
            continue
        if nb >= min_blocks:
            pcm = brr.decode(data[pos:pos + nb * brr.BLOCK])
            r, peak, rms = _coherence(pcm, min_peak)
            if r >= min_coherence and peak >= min_peak and rms >= min_rms:
                yield {"offset": pos, "coherence": round(r, 4), "peak": peak,
                       "rms": round(rms), "blocks": nb, "pcm": pcm}
                yielded += 1
                if yielded >= max_samples:
                    return
        pos += nb * brr.BLOCK                           # skip the whole sample, stay aligned
