"""A fast path must produce the same answer as the slow one it replaces.

Two optimisations from the performance audit, both of which change HOW a value
is computed and must not change the value:

  `integrity._effective_bits` ORed every PCM sample in a Python loop --
  `int.from_bytes` called 28.7 million times over an 80-file audit -- to compute
  one OR reduction. The numpy path does it per byte-position and reassembles.

  `validate` slurped whole files where `audit` mapped the same bytes, so its
  footprint scaled with input while audit's stayed flat.

numpy stays OPTIONAL. This check runs on a base install today, so a hard
dependency would quietly move `audit` behind the [analysis] extra.
"""

import struct

import pytest

from acidcat.core.forensics import integrity as I


def _loop(data, spans, bps, order):
    """The pure-Python definition, stated here independently of the module so
    the test cannot drift with the code it checks."""
    acc = examined = 0
    for lo, hi in spans:
        end = lo + ((hi - lo) // bps) * bps
        for p in range(lo, end, bps):
            acc |= int.from_bytes(data[p:p + bps], order, signed=False)
            examined += 1
    return acc, examined


@pytest.mark.parametrize("bps", [1, 2, 3, 4])
@pytest.mark.parametrize("order", ["little", "big"])
@pytest.mark.parametrize("pad", [0, 3, 8])
def test_numpy_or_matches_the_loop(bps, order, pad):
    """Across every sample width, both byte orders, and low-bit padding -- the
    padding being the thing the check exists to detect."""
    pytest.importorskip("numpy")
    if pad >= bps * 8:
        pytest.skip("padding wider than the sample")
    raw = bytearray()
    for i in range(600):
        v = ((i * 2654435761) & ((1 << (bps * 8 - pad)) - 1)) << pad
        raw += v.to_bytes(bps, order)
    data = bytes(raw)
    spans = [(0, len(data))]
    assert I._or_reduce_numpy(data, spans, bps, order) == _loop(data, spans, bps, order)


@pytest.mark.parametrize("extra", [1, 2, 3])
def test_a_partial_tail_sample_is_not_counted(extra):
    """The span end is clamped to the DECLARED data end, which need not be a
    whole number of samples on a malformed file -- acidcat's whole subject. The
    loop used to count a short tail as a sample, so `examined` over-reported by
    one per span. It is compared against 1024 and printed to the user, so both
    paths now stop at the last complete sample.
    """
    pytest.importorskip("numpy")
    bps = 3
    data = bytes(range(256)) * 4 + bytes(extra)
    spans = [(0, len(data))]
    fast = I._or_reduce_numpy(data, spans, bps, "little")
    assert fast == _loop(data, spans, bps, "little")
    assert fast[1] == (len(data) // bps)          # whole samples only


@pytest.mark.parametrize("bps", [1, 2, 3, 4])
def test_a_span_past_the_end_of_the_buffer(bps):
    """A DECLARED size that overruns the file is the commonest damage acidcat
    describes, so it reaches here as a span past the end of the buffer.

    Every span in this test file was (0, len(data)), so neither path was ever
    asked for bytes that are not there. numpy raised ValueError -- a raw
    traceback out of `audit` on any truncated WAV -- and the loop did something
    quieter and worse: sliced short and counted samples that do not exist.
    """
    pytest.importorskip("numpy")
    data = bytes(range(256)) * 4
    spans = [(0, len(data) + 500)]                # declared far past the end
    fast = I._or_reduce_numpy(data, spans, bps, "little")
    assert fast is not None
    assert fast[1] == len(data) // bps            # only samples that exist

    # The reference loop is deliberately naive, and here it shows why the clamp
    # cannot live in one path only: given the same overrunning span it walks off
    # the end and reports MORE samples than the buffer holds.
    assert _loop(data, spans, bps, "little")[1] > len(data) // bps
    # against a span clamped to the buffer, the two agree exactly
    assert fast == _loop(data, [(0, len(data))], bps, "little")


def test_effective_bits_clamps_a_declared_size_past_the_end():
    """The clamp belongs in the caller, so BOTH paths see a real span."""
    data = bytes(range(256)) * 4
    eff, examined = I._effective_bits(data, 0, len(data) + 10_000, 2, "little")
    assert examined == len(data) // 2
    assert eff is None or 0 < eff <= 16


def test_effective_bits_clamps_on_the_fallback_path_too(monkeypatch):
    """Without numpy there is no crash to notice, only a wrong number.

    A guard inside the numpy helper alone would leave the fallback counting
    samples past the end of the buffer, and `examined` is printed to the user
    as "{n:,} samples" and gated against 1024 -- so the base install, which is
    the one WITHOUT numpy, would quietly report a bit depth it derived partly
    from bytes that do not exist.
    """
    import builtins
    real = builtins.__import__

    def blocked(name, *a, **k):
        if name == "numpy":
            raise ImportError("numpy is not installed")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    data = bytes(range(256)) * 4
    assert I._or_reduce_numpy(data, [(0, 4)], 2, "little") is None    # fallback live
    _eff, examined = I._effective_bits(data, 0, len(data) + 10_000, 2, "little")
    assert examined == len(data) // 2


def test_multiple_spans_accumulate():
    """_effective_bits samples 64 blocks end-to-end, not one range."""
    pytest.importorskip("numpy")
    data = bytes(range(256)) * 8
    spans = [(0, 300), (400, 700), (900, 1200)]
    assert I._or_reduce_numpy(data, spans, 2, "little") == _loop(data, spans, 2, "little")


def test_falls_back_when_numpy_is_absent(monkeypatch):
    """The fallback is mandatory, not decorative: this check runs on a base
    install, where numpy is not present."""
    import builtins
    real = builtins.__import__

    def blocked(name, *a, **k):
        if name == "numpy":
            raise ImportError("numpy is not installed")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert I._or_reduce_numpy(b"\x01\x02\x03\x04", [(0, 4)], 2, "little") is None


def test_effective_bits_still_detects_padding(tmp_path):
    """The end-to-end behaviour, through the public function: 24-bit samples
    carrying only 16 bits of real data must be reported as 16-bit effective."""
    n = 4096
    # little-endian 24-bit: the LOW byte comes first, so a leading \x00 is what
    # makes the bottom 8 bits always zero (16 bits of real data in a 24-bit
    # container). Getting this backwards zeroes the HIGH byte instead, which is
    # not padding at all -- and the first version of this test did exactly that.
    raw = b"".join(b"\x00" + ((i * 7919) & 0xFFFF).to_bytes(2, "little")
                   for i in range(n))
    eff, examined = I._effective_bits(raw, 0, len(raw), 3, "little")
    assert examined == n
    assert eff == 16


def test_validate_and_audit_agree_on_the_same_file(tmp_path):
    """validate now maps like audit does; the verdict must be unchanged."""
    from acidcat.core.write import constraints
    from acidcat.core.infra.mapped import map_file

    pcm = b"\x11\x22" * 4096
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    raw = bytearray(b"RIFF" + struct.pack("<I", len(body)) + body)
    struct.pack_into("<I", raw, 4, 999999)        # a stale size to find
    p = tmp_path / "b.wav"
    p.write_bytes(bytes(raw))

    slurped = constraints.analyze(p.read_bytes())
    data, close = map_file(str(p))
    try:
        with memoryview(data) as v:
            mapped = constraints.analyze(v)
    finally:
        close()
    assert [x.describe() for x in slurped.violations] == \
           [x.describe() for x in mapped.violations]
