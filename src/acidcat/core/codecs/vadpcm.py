"""Nintendo 64 VADPCM -- the RSP's vector ADPCM codec.

The N64 audio DSP decodes 9-byte frames into 16 samples using a per-sample
*codebook*: for each of `npredictors` predictors, `order` rows of 8 int16
coefficients (the encoder-precomputed impulse response of an order-N predictor
over an 8-sample window). A frame's header byte picks one predictor and a shift;
the 8 data bytes carry sixteen signed 4-bit residuals. Decode runs two 8-sample
subvectors, carrying the last `order` outputs as history.

Both the classic libultra ALBank and the newer SM64-style sound engine share
this codec (ALADPCMBook / AdpcmBook); only the bank wrapper differs.

Two fidelity notes matched to the RSP (not the SDK C tool): the accumulator
`>> 11` is arithmetic (floor toward -inf), and every output is clamped to int16
-- the hardware saturates, so a game-matching decode must too.

Reference: N64 SDK `vdecode.c` (vdecodeframe) / `vpredictor.c` (inner_product);
depp/skelly64 VADPCM writeup.

    from acidcat.core.codecs import vadpcm
    pcm = vadpcm.decode(frames, book_coefs, order, npredictors)   # 16-bit mono PCM
"""

import array
import struct

FRAME = 9                                # 1 header byte + 8 data bytes -> 16 samples


def parse_book(data, off=0):
    """Read an ADPCM codebook at `off`: (order, npredictors, coefs) where coefs
    is a flat list of order*npredictors*8 big-endian int16. Raises on bad shape."""
    order, npred = struct.unpack_from(">ii", data, off)
    if not (1 <= order <= 8 and 1 <= npred <= 16):
        raise ValueError(f"implausible codebook order={order} npred={npred}")
    n = order * npred * 8
    coefs = list(struct.unpack_from(">" + str(n) + "h", data, off + 8))
    return order, npred, coefs


def _clamp16(v):
    return -32768 if v < -32768 else (32767 if v > 32767 else v)


def decode(data, coefs, order, npredictors, history=None, samples=None):
    """Decode VADPCM `data` to 16-bit mono PCM bytes. `coefs` is the flat codebook
    (order*npredictors*8 int16). `history` seeds the last `order` outputs (a loop
    ALADPCMloop.state[]; defaults to silence). Stops after `samples` if given."""
    out = array.array("h")
    hist = [0] * order if history is None else list(history[-order:])
    n = len(data)
    pos = 0
    limit = samples if samples is not None else None
    while pos + FRAME <= n:
        header = data[pos]
        shift = header >> 4
        pidx = header & 0x0F
        if pidx >= npredictors:                       # malformed frame -> stop cleanly
            break
        ix = [0] * 16
        for i in range(8):
            c = data[pos + 1 + i]
            ix[2 * i] = c >> 4
            ix[2 * i + 1] = c & 0x0F
        for i in range(16):
            if ix[i] >= 8:
                ix[i] -= 16
        base = pidx * order * 8                        # coefs[base] = book[pidx][0][0]
        last = base + (order - 1) * 8                  # the order-1 impulse row
        for s in range(2):
            res = ix[s * 8:s * 8 + 8]
            acc = [0] * 8
            for r in range(order):
                prev = hist[r]
                row = base + r * 8
                for j in range(8):
                    acc[j] += coefs[row + j] * prev
            outv = [0] * 8
            for i in range(8):
                scaled = res[i] << shift
                val = _clamp16((acc[i] >> 11) + scaled)
                outv[i] = val
                out.append(val)
                for j in range(7 - i):                 # propagate this residual forward
                    acc[i + 1 + j] += coefs[last + j] * scaled
            hist = outv[8 - order:8]
        pos += FRAME
        if limit is not None and len(out) >= limit:
            break
    if limit is not None:
        del out[limit:]
    return out.tobytes()
