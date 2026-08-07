"""Entropy over a file, without reading the file.

The entropy view is most wanted on exactly the files least suited to being read
into memory -- a disk image, a long capture. `windowed_entropy` takes bytes you
already hold, so the view could only ever be as big as RAM. This is the same
measurement, streamed.

Two properties matter more than the speed:

  Unsampled, it must be byte-identical to `windowed_entropy`. Two functions
  answering one question is fine; two answering it differently is the drift
  this codebase keeps finding.

  Sampled, it must SAY so. A sampled curve is an estimate, and a viewer that
  draws it identically to an exact one states a measurement nobody made.
"""

import os

import pytest

from acidcat.core.forensics import viz


def _write(path, data):
    path.write_bytes(data)
    return str(path)


def test_unsampled_is_identical_to_the_in_memory_definition(tmp_path):
    """The anti-drift invariant. Same boundaries, same numbers, or they will
    diverge quietly the first time either is touched."""
    data = bytes((i * 31 + (i // 97)) % 256 for i in range(40_000))
    p = _write(tmp_path / "a.bin", data)
    got, size, sampled = viz.file_entropy(p, windows=72)
    assert sampled is False
    assert size == len(data)
    assert got == viz.windowed_entropy(data, 72)


def test_a_sampled_run_says_it_sampled(tmp_path):
    p = _write(tmp_path / "big.bin", bytes(range(256)) * 8000)   # ~2 MB
    _vals, _size, sampled = viz.file_entropy(p, windows=8, sample=1024)
    assert sampled is True, "estimated the curve and reported it as measured"


def test_an_empty_file_is_not_a_crash(tmp_path):
    vals, size, sampled = viz.file_entropy(_write(tmp_path / "z.bin", b""))
    assert (vals, size, sampled) == ([], 0, False)


def test_it_does_not_read_the_whole_file(tmp_path, monkeypatch):
    """The entire point. Count the bytes actually read."""
    p = _write(tmp_path / "c.bin", os.urandom(4_000_000))
    real_open = open
    total = {"n": 0}

    class _Counting:
        def __init__(self, fh):
            self._fh = fh

        def read(self, n=-1):
            b = self._fh.read(n)
            total["n"] += len(b)
            return b

        def __getattr__(self, k):
            return getattr(self._fh, k)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self._fh.__exit__(*a)

    monkeypatch.setattr("builtins.open",
                        lambda *a, **k: _Counting(real_open(*a, **k)))
    _vals, size, sampled = viz.file_entropy(p, windows=64, sample=4096)
    assert sampled is True
    assert total["n"] < size // 4, f"read {total['n']:,} of {size:,}"


def test_a_payload_at_the_END_of_a_window_is_seen(tmp_path):
    """Why the sampled window is probed at several points, not just its head.

    Head-only sampling is one seek cheaper and systematically blind to the case
    the view exists to catch: a low-entropy container followed by appended
    high-entropy data puts the interesting bytes at the END of a window.
    """
    win = 200_000
    quiet = b"\x00\x01\x02\x03" * (win // 4)          # low entropy
    loud = os.urandom(win)                             # ~8 bits/byte
    p = _write(tmp_path / "poly.bin", quiet + loud)

    vals, _size, sampled = viz.file_entropy(p, windows=2, sample=4096)
    assert sampled is True
    assert len(vals) == 2
    assert vals[1] > 7.0, f"missed the appended payload: {vals[1]:.2f} bits/byte"
    assert vals[0] < 3.0, f"quiet half should be low: {vals[0]:.2f}"

    # and the tail must be visible even when it is a MINORITY of its window
    mostly_quiet = b"\x00\x01\x02\x03" * (win // 4) + os.urandom(win // 4)
    q = _write(tmp_path / "tail.bin", mostly_quiet)
    tail_vals, _s, _samp = viz.file_entropy(q, windows=1, sample=4096)
    head_only = viz.windowed_entropy(open(q, "rb").read(4096), 1)
    assert tail_vals[0] > head_only[0] + 0.5, (
        "probing only the head would have reported this window as quiet")


@pytest.mark.parametrize("windows", [1, 2, 7, 72, 200])
def test_the_window_count_is_honoured_or_bounded_by_size(tmp_path, windows):
    p = _write(tmp_path / "w.bin", bytes(range(256)) * 40)
    vals, _size, _s = viz.file_entropy(p, windows=windows)
    assert len(vals) == min(windows, len(vals)) and vals
    assert all(0.0 <= v <= 8.0 for v in vals)


def test_it_is_public():
    """The reason the streaming version had to land before 1.0: viz is public,
    so adding it afterwards means either a breaking change or two functions
    forever."""
    import acidcat
    assert hasattr(acidcat.viz, "file_entropy")


def test_entropy_stays_inside_its_documented_range():
    """Found by the window-count test above, and older than the streaming code.

    A perfectly uniform distribution is exactly log2(k) bits, but the identity
    H = log2(N) - (1/N)*sum(c*log2(c)) lands a few ulp above it, so byte data
    returned 8.000000000000014 against a docstring promising 0.0 .. 8.0. The
    ceiling is log2 of the symbols actually present, which is correct for any
    alphabet rather than a hardcoded 8.
    """
    from acidcat.core.primitives.signal import byte_entropy, entropy_from_counts

    assert entropy_from_counts([1] * 256, 256) == 8.0
    assert entropy_from_counts([5, 5], 10) == 1.0          # two symbols -> 1 bit
    assert entropy_from_counts([9], 9) == 0.0              # certain -> 0 bits
    assert byte_entropy(bytes(range(256))) == 8.0
    assert all(0.0 <= v <= 8.0 for v in viz.windowed_entropy(bytes(range(256)) * 40, 2))
