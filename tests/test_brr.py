"""Tests for the SNES BRR codec (core.brr) and container-agnostic recovery
(core.snesrip). The codec's residual/shift path is pinned exactly with a
filter-0 block (no prediction isolates the scaling); the filter recurrence and
the end-flag/clip behaviour are pinned against regression; recovery is checked
on a synthetic sample embedded between invalid-header padding."""
import array
import random

from acidcat.core import snesrip
from acidcat.core.codecs import brr


def _block(shift, filt, nibbles, loop=False, end=False):
    """Assemble one 9-byte BRR block from 16 signed 4-bit residuals."""
    assert len(nibbles) == 16
    header = (shift << 4) | (filt << 2) | (loop << 1) | (end & 1)
    body = bytes(((nibbles[2 * i] & 0x0F) << 4) | (nibbles[2 * i + 1] & 0x0F)
                 for i in range(8))
    return bytes([header]) + body


def _pcm(b):
    a = array.array("h"); a.frombytes(b); return list(a)


def test_block_valid():
    assert brr.block_valid(0x00) and brr.block_valid(0xC0)      # shift 0, 12
    assert not brr.block_valid(0xD0) and not brr.block_valid(0xF0)  # shift 13, 15


def test_filter0_is_scaled_residual():
    # filter 0 has no prediction, so each output is just (residual << shift) >> 1.
    # shift 2 -> sample == residual * 2; here residuals are all +1 then the block
    # ends, so 16 samples of value 2.
    pcm = _pcm(brr.decode(_block(2, 0, [1] * 16, end=True)))
    assert pcm == [2] * 16


def test_filter0_sign_extend():
    # residual 0x8 is -8; shift 4 -> (-8 << 4) >> 1 = -64.
    pcm = _pcm(brr.decode(_block(4, 0, [0x8] * 16, end=True)))
    assert pcm == [-64] * 16


def test_end_flag_stops_decode():
    # two blocks, the first end-flagged: stop_on_end decodes only the first 16.
    data = _block(2, 0, [1] * 16, end=True) + _block(2, 0, [3] * 16, end=True)
    assert len(_pcm(brr.decode(data))) == 16
    assert len(_pcm(brr.decode(data, stop_on_end=False))) == 32


def test_invalid_shift_stops_cleanly():
    # a block whose shift is an invalid range (>12) halts decode without raising.
    assert brr.decode(_block(15, 0, [1] * 16)) == b""


def test_filter1_prediction_accumulates():
    # filter 1 adds ~15/16 of the previous sample, so a constant positive residual
    # ramps upward rather than staying flat (unlike filter 0).
    pcm = _pcm(brr.decode(_block(6, 1, [1] * 16, end=True)))
    assert pcm[0] < pcm[-1]                       # the IIR integrates the residual
    assert all(pcm[i] <= pcm[i + 1] for i in range(len(pcm) - 1))


def _synth_sample(nblocks=40, shift=11):
    """A coherent BRR sample: a slow triangle in the residuals (filter 0) so the
    decoded wave is smooth and loud, end-flagged on the last block."""
    blocks = []
    period = 30
    for b in range(nblocks):
        nibs = []
        for k in range(16):
            i = b * 16 + k
            p = i % period
            v = (p if p < period // 2 else period - p) - 8      # triangle in -8..7
            nibs.append(v & 0x0F)
        blocks.append(_block(shift, 0, nibs, end=(b == nblocks - 1)))
    return b"".join(blocks)


def test_recover_finds_embedded_sample():
    sample = _synth_sample()
    pad = b"\xff" * 200                            # 0xFF headers are invalid -> skipped fast
    rom = pad + sample + pad
    hits = list(snesrip.recover(rom, min_blocks=16))
    assert len(hits) == 1
    h = hits[0]
    assert h["offset"] == len(pad)
    assert h["blocks"] == 40
    assert h["coherence"] >= 0.9 and h["peak"] >= 600


def test_recover_rms_floor_gates_on_loudness():
    # the rms floor drops coherent-but-quiet false positives. Verify the gate is
    # wired: a loud sample passes the default floor, but a floor above its rms
    # rejects it while a zero floor keeps it (min_rms independent of peak/coherence).
    sample = _synth_sample()                       # loud triangle, rms in the thousands
    rom = b"\xff" * 200 + sample + b"\xff" * 200
    kept = list(snesrip.recover(rom))                       # default min_rms=500
    assert len(kept) == 1 and kept[0]["rms"] >= 500
    assert list(snesrip.recover(rom, min_rms=99999)) == []   # unreachable floor: dropped
    assert len(list(snesrip.recover(rom, min_rms=0))) == 1   # no floor: kept


def test_recover_terminates_on_zero_padding():
    # 0x00 is a valid shift-0 header with no end flag, so a zero region is a long
    # valid-but-unterminated run. The old byte-at-a-time resync was O(n*max_blocks)
    # and hung here; the span-skip must finish immediately with no hits. (If this
    # regresses it does not fail -- it hangs -- which is the signal.)
    assert list(snesrip.recover(b"\x00" * (512 * 1024))) == []


def test_recover_skips_junk_run_then_finds_sample():
    # a valid-but-unterminated run of >= min_blocks (junk, no end flag) is skipped
    # as a span; a real sample after the break must still be found (the span-skip
    # must not overshoot it).
    junk = _block(2, 0, [1] * 16) * 20             # 20 valid blocks, no end flag
    rom = junk + b"\xff" + _synth_sample() + b"\xff" * 64
    hits = list(snesrip.recover(rom))
    assert len(hits) == 1 and hits[0]["offset"] == len(junk) + 1


def test_decode_tolerates_short_history():
    blk = _block(2, 0, [1] * 16, end=True)
    assert brr.decode(blk, history=[100])          # 1-element: no IndexError
    assert brr.decode(blk, history=[])             # empty: no IndexError


def test_recover_rejects_noise():
    rng = random.Random(0xB44)
    noise = bytes(rng.randrange(256) for _ in range(0x4000))
    # random data has no coherent, end-flag-terminated, loud run to recover
    assert list(snesrip.recover(noise)) == []


def test_recover_strips_copier_header():
    sample = _synth_sample()
    body = b"\xff" * 100 + sample
    rom = body + b"\xff" * (1024 - len(body))      # a real ROM is a 1024-multiple
    with_hdr = b"\x00" * 512 + rom                 # +512 -> size % 1024 == 512, a copier header
    hits = list(snesrip.recover(with_hdr))
    assert hits and hits[0]["offset"] == 100       # offset is into the stripped ROM
