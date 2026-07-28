"""SNES BRR -- the SPC700/S-DSP's Bit Rate Reduction ADPCM codec.

The SNES sound chip decodes 9-byte blocks into 16 samples. A block is one header
byte plus eight data bytes; each data byte holds two signed 4-bit residuals (high
nibble first). The header carries a shift (high nibble, 0..12), a 2-bit filter
selector, a loop flag (bit 1) and an *end* flag (bit 0) that marks the block as
the last of a sample.

Unlike N64 VADPCM there is **no codebook**: the four filters are fixed
second-order predictors over the previous two decoded samples, so every sample is
self-describing. That makes BRR both simpler to decode and easier to recover from
a ROM -- an end-flag-terminated run of valid blocks that decodes to coherent audio
is a sample, with nothing external to pair it against.

Fidelity notes matched to the S-DSP: the decode domain is 15-bit signed -- each
output is clamped to int16 then wrapped to 15 bits (the audible BRR overflow), and
that wrapped value is what both feeds the filter history and is emitted. Filter
coefficients are the standard integer forms (15/16, 61/32-15/16, 115/64-13/16).

Reference: fullsnes (Nocash) S-DSP notes; DMV27 snesbrr decoder.

    from acidcat.core import brr
    pcm = brr.decode(block_bytes)                    # 16-bit mono PCM (little-endian)
"""

import array

BLOCK = 9                                # 1 header byte + 8 data bytes -> 16 samples
MAX_SHIFT = 12                           # shifts 13..15 are invalid range codes


def block_valid(header):
    """A block header whose shift is a real range (<= 12). The filter is always a
    valid 0..3, so the shift nibble is the only structural gate."""
    return (header >> 4) <= MAX_SHIFT


def _clip15(v):
    """Clamp to int16, then wrap to 15-bit signed -- the S-DSP decode domain (the
    wrap is the characteristic BRR overflow glitch, kept for a hardware match)."""
    if v > 32767:
        v = 32767
    elif v < -32768:
        v = -32768
    v &= 0x7FFF
    return v - 0x8000 if v >= 0x4000 else v


def decode(data, history=None, samples=None, stop_on_end=True):
    """Decode BRR `data` to 16-bit mono PCM bytes. `history` seeds the previous
    two outputs (p1, p2) as (older, oldest); defaults to silence. Stops at the
    first block with the end flag when `stop_on_end` (a whole sample), after
    `samples` outputs if given, or at a block whose shift is an invalid range."""
    out = array.array("h")
    # last two outputs seed the filter: (older, newer). Pad so a short/None history
    # is silence rather than an IndexError.
    p2, p1 = ([0, 0] + list(history or ())[-2:])[-2:]
    n = len(data)
    pos = 0
    while pos + BLOCK <= n:
        header = data[pos]
        shift = header >> 4
        if shift > MAX_SHIFT:                          # invalid range -> stop cleanly
            break
        filt = (header >> 2) & 0x03
        for i in range(8):
            byte = data[pos + 1 + i]
            for nib in (byte >> 4, byte & 0x0F):
                s = nib - 16 if nib >= 8 else nib      # sign-extend the 4-bit residual
                s = (s << shift) >> 1                   # shift is 0..12 (invalid range broke above)
                if filt == 1:
                    s += p1 + ((-p1) >> 4)                              # p1 * 15/16
                elif filt == 2:
                    s += (p1 << 1) + ((-(p1 + (p1 << 1))) >> 5) \
                        - p2 + (p2 >> 4)                               # 61/32, -15/16
                elif filt == 3:
                    s += (p1 << 1) + ((-(p1 + (p1 << 2) + (p1 << 3))) >> 6) \
                        - p2 + (((p2 << 1) + p2) >> 4)                 # 115/64, -13/16
                s = _clip15(s)
                out.append(s)
                p2, p1 = p1, s
        pos += BLOCK
        if stop_on_end and (header & 0x01):            # end flag -> last block
            break
        if samples is not None and len(out) >= samples:
            break
    if samples is not None:
        del out[samples:]
    return out.tobytes()
