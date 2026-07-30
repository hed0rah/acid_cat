"""Container-agnostic N64 VADPCM sample recovery.

N64 games each wrap VADPCM in their own bank format -- classic libultra ALBank,
SM64 ALSeqFile, Zelda AudioTable, Camelot's PtrTables, and more -- so there is no
one container to parse. But the codec is universal and the pieces are findable by
structure, so we recover the audio without the container:

  1. scan the ROM for ADPCM codebooks (order-2 AdpcmBook: order==2, small
     npredictors, plausible coefficients);
  2. find the audiotable regions by VADPCM frame density (the 9-byte frames'
     scale nibble stays in range across a run);
  3. pair each sample start with the codebook that decodes it to *loud and
     coherent* audio (peak AND autocorrelation -- autocorrelation alone is fooled
     by silence), and decode with core/vadpcm.py.

This is "PhotoRec for audio" applied to an N64 ROM: it does not reconstruct
instrument names or the bank tree, it rescues the raw samples. Byte order is
normalized off the ROM magic (z64/n64/v64).

    from acidcat.core.extract import n64rip
    for s in n64rip.recover(rom_bytes):
        open(s["name"] + ".wav", "wb").write(s["wav"])   # (samples.py wraps to WAV)
"""

import array
import struct

from acidcat.core.codecs import vadpcm
from acidcat.core.primitives import signal

_Z64 = b"\x80\x37\x12\x40"
_V64 = b"\x37\x80\x40\x12"
_N64 = b"\x40\x12\x37\x80"


def is_n64_rom(head):
    return head[:4] in (_Z64, _V64, _N64)


def normalize(data):
    """Return the ROM as big-endian (z64). Handles v64 (byte-swapped) and n64
    (word-reversed); leaves an already-z64 image untouched."""
    m = data[:4]
    if m == _V64:
        b = bytearray(data)
        b[0::2], b[1::2] = data[1::2], data[0::2]
        return bytes(b)
    if m == _N64:
        a = array.array("I")
        a.frombytes(data[:len(data) // 4 * 4])
        a.byteswap()
        return a.tobytes()
    return data


def find_codebooks(data):
    """Yield (offset, npredictors, coefs) for every plausible order-2 AdpcmBook."""
    out = []
    for off in range(0, len(data) - 16, 4):
        order, npred = struct.unpack_from(">ii", data, off)
        if order != 2 or not (1 <= npred <= 8):
            continue
        n = order * npred * 8
        coefs = struct.unpack_from(">" + str(n) + "h", data, off + 8)
        if all(-6000 < v < 6000 for v in coefs) and sum(v != 0 for v in coefs) > n * 0.55:
            out.append((off, npred, list(coefs)))
    return out


def _coherence(pcm, min_peak):
    # mean-centered lag-1 autocorrelation (~1 = coherent audio, ~0 = noise), with
    # a peak gate and this recovery's 6000-sample floor. Shared with snesrip.
    return signal.pcm_coherence(pcm, min_peak, min_len=6000)[0]


def _audiotable_regions(data, win=0x1000, thresh=0.985):
    """Coarse frame-density scan: contiguous spans where the 9-byte VADPCM frame
    headers' scale nibble stays in range. The audiotable is a small fraction of a
    ROM, so restricting the sample sweep to these spans is the main speedup."""
    regions = []
    step = 0x400
    for s in range(0x1000, len(data) - win, step):
        ok = sum(1 for i in range(0, win, 9) if (data[s + i] >> 4) <= 12) / (win // 9)
        if ok > thresh:
            if regions and s - regions[-1][1] < 0x2000:
                regions[-1][1] = s + win
            else:
                regions.append([s, s + win])
    return [(a, b) for a, b in regions if b - a >= 0x800]


def recover(data, stride=0x1000, min_peak=700, min_coherence=0.94,
            book_stride=1, max_samples=256):
    """Recover coherent VADPCM samples from an N64 ROM. Yields dicts with pcm
    (16-bit mono bytes), offset, coherence, peak. Byte order is normalized first.

    Only the audiotable spans are swept, and each pairing is scored on a short
    prefix, so an 8-64 MB ROM finishes in a few seconds."""
    data = normalize(data)
    books = find_codebooks(data)
    if not books:
        return
    # dedup codebooks by their coefficients -- games repeat the same books and the
    # loose scan yields identical false positives; unique books keep coverage small.
    uniq = {}
    for _, npred, c in books[::book_stride]:
        uniq.setdefault((npred, tuple(c)), (npred, c))
    coefs_list = list(uniq.values())
    regions = _audiotable_regions(data)

    budget = 60000                                     # cap total scoring decodes
    seen = set()
    yielded = 0
    for ra, rb in regions:
        if budget <= 0:
            break
        for start in range(ra, rb, stride):
            if budget <= 0:
                break
            # score every unique codebook on a medium prefix (past the mid-sample
            # transient), keep the best; then full-decode the winner.
            head = data[start:start + 0x1800]
            best_r, best_np, best_c = 0.0, 0, None
            for npred, c in coefs_list:
                budget -= 1
                r = _coherence(vadpcm.decode(head, c, 2, npred, samples=6000), min_peak)
                if r > best_r:
                    best_r, best_np, best_c = r, npred, c
            if best_r < min_coherence or best_c is None:
                continue
            pcm = vadpcm.decode(data[start:start + 0x10000], best_c, 2, best_np)
            best_r = _coherence(pcm[:24000], min_peak)
            s = array.array("h"); s.frombytes(pcm)
            peak = max((abs(x) for x in s[:12000]), default=0)
            key = (len(s) // 1500, peak // 300)        # dedup near-duplicate rips
            if key in seen:
                continue
            seen.add(key)
            yield {"offset": start, "coherence": round(best_r, 4), "peak": peak,
                   "frames": len(s), "pcm": pcm}
            yielded += 1
            if yielded >= max_samples:
                return
