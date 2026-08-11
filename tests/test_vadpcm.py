"""Tests for core.vadpcm (Nintendo 64 vector ADPCM).

The codec's math is pinned by crafted codebooks that isolate each term, since a
realistic order-2 codebook is an encoder-precomputed impulse response that cannot
be hand-fabricated meaningfully. A book of all-2048 (= 1.0 in Q11) turns the
predictor into a pure integrator, which gives an exact, checkable trace."""
import array

from acidcat.core.codecs import vadpcm


def _decode(book, order, npred, hist, frame):
    pcm = vadpcm.decode(bytes(frame), book, order, npred, history=hist)
    s = array.array("h"); s.frombytes(pcm)
    return list(s)


# one frame: header 0x00 (shift 0, predictor 0), then residual[0]=nibble
_F_RES1 = [0x00, 0x10, 0, 0, 0, 0, 0, 0, 0]        # residuals: 1, then fifteen 0


def test_order1_integrator():
    # order 1, coef 2048 -> integrator. history 100, residual 1 -> 101 held.
    out = _decode([2048] * 8, 1, 1, [100], _F_RES1)
    assert out[:8] == [101] * 8


def test_order2_history_newest():
    # book[0]=0, book[1]=2048 -> acc uses history[1] (newest). hist[1]=100 -> 101.
    out = _decode([0] * 8 + [2048] * 8, 2, 1, [0, 100], _F_RES1)
    assert out[:8] == [101] * 8


def test_order2_history_oldest_no_propagate():
    # book[0]=2048, book[1]=0 -> acc uses history[0] (oldest); book[order-1]=0 so a
    # freshly decoded residual does NOT propagate forward.
    out = _decode([2048] * 8 + [0] * 8, 2, 1, [100, 0], _F_RES1)
    assert out[:8] == [101, 100, 100, 100, 100, 100, 100, 100]


def test_shift_and_propagation():
    # header 0x30 = shift 3; residual 1 -> 1<<3 = 8; propagated forward by book[1].
    out = _decode([0] * 8 + [2048] * 8, 2, 1, [0, 0], [0x30, 0x10, 0, 0, 0, 0, 0, 0, 0])
    assert out[0] == 8 and out[1] == 8


def test_history_carries_across_subvectors():
    # residual 5 in sample 0, integrator -> all 16 samples of the frame are 5
    # (proves history carries from the first 8-sample subvector into the second).
    out = _decode([0] * 8 + [2048] * 8, 2, 1, [0, 0], [0x00, 0x50, 0, 0, 0, 0, 0, 0, 0])
    assert out == [5] * 16


def test_clamp16():
    # a large history through a unit predictor saturates to int16, matching the RSP.
    out = _decode([0] * 8 + [2048] * 8, 2, 1, [0, 40000], _F_RES1)
    assert out[0] == 32767                              # 40000 clamped


def test_parse_book():
    import struct
    blob = struct.pack(">ii", 2, 1) + struct.pack(">16h", *range(16))
    order, npred, coefs = vadpcm.parse_book(blob)
    assert order == 2 and npred == 1 and coefs == list(range(16))


def test_malformed_predictor_stops():
    # a frame whose predictor index >= npredictors is not decoded (clean stop).
    out = _decode([2048] * 8, 1, 1, [0], [0x05, 0x10, 0, 0, 0, 0, 0, 0, 0])   # pred 5 >= 1
    assert out == []
