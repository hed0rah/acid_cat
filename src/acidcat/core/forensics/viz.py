"""Zero-dependency terminal visualization primitives for byte dissection.

These give a picture of a file's *shape*: where the structure is, where the
high-entropy (encrypted / compressed) regions are, and how a byte-class map
(binvis-style) lays the file out. Pure functions returning plain strings / grids;
the caller adds color. No third-party imports.

  braille_line(values, w, h)   -> h strings (a smooth line/area plot)
  byte_histogram(data, w, h)   -> braille bars of the 256-value distribution
  windowed_entropy(data, n)    -> n Shannon-entropy samples (bits/byte, 0..8)
  file_entropy(path, n)        -> the same, streamed from disk; also reports
                                  whether it sampled instead of reading whole
  hilbert_grid(data, order)    -> (side x side) grid of mean bytes along a
                                  Hilbert curve, so adjacent offsets stay adjacent
  byte_class(b)                -> a (glyph, class_name) for a byte's binvis class
                                  (class_name is a tui_theme.BYTE_CLASS key; color
                                  lives in the theme, not here)
"""


import os

from acidcat.core.primitives.signal import byte_counts, entropy_from_counts

# dot bit within a 2x4 braille cell, indexed [row 0..3][col 0..1]
_DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def _dot_rows(dots, width, height):
    rows = []
    for cy in range(height):
        line = []
        for cx in range(width):
            m = 0
            for dy in range(4):
                for dx in range(2):
                    if (cx * 2 + dx, cy * 4 + dy) in dots:
                        m |= _DOTS[dy][dx]
            line.append(chr(0x2800 + m))
        rows.append("".join(line))
    return rows


def braille_line(values, width=72, height=8, vmin=None, vmax=None, fill=False):
    """Braille line (or filled area) plot, `height` strings top-first."""
    if not values or width < 1 or height < 1:
        return [" " * max(1, width) for _ in range(max(1, height))]
    dot_w, dot_h = width * 2, height * 4
    vmin = min(values) if vmin is None else vmin
    vmax = max(values) if vmax is None else vmax
    span = (vmax - vmin) or 1.0
    n = len(values)
    dots = set()
    prev = None
    for x in range(dot_w):
        idx = int(round(x * (n - 1) / (dot_w - 1))) if dot_w > 1 and n > 1 else 0
        v = values[min(n - 1, idx)]
        from_bottom = int(round((v - vmin) / span * (dot_h - 1)))
        from_bottom = max(0, min(dot_h - 1, from_bottom))
        top = (dot_h - 1) - from_bottom
        dots.add((x, top))
        if prev is not None:
            lo, hi = sorted((prev, top))
            for yy in range(lo, hi + 1):
                dots.add((x, yy))
        if fill:
            for yy in range(top, dot_h):
                dots.add((x, yy))
        prev = top
    return _dot_rows(dots, width, height)


def byte_histogram(data, width=128, height=6):
    """Braille bar chart of the byte distribution. Flat top = encrypted/
    compressed; peaks = structure."""
    counts = byte_counts(data)
    return braille_line(counts, width=width, height=height, vmin=0, fill=True)


def windowed_entropy(data, windows=72):
    """Shannon entropy (bits/byte, 0..8) over ``windows`` equal slices. A flat
    line near 8 is an encrypted or compressed span; structure varies."""
    n = len(data)
    if n == 0:
        return [0.0] * windows
    out = []
    for i in range(windows):
        lo = i * n // windows
        hi = max(lo + 1, (i + 1) * n // windows)
        seg = data[lo:hi]
        counts = byte_counts(seg)
        out.append(entropy_from_counts(counts, len(seg)))
    return out


_ENTROPY_SAMPLE = 8192          # bytes read per window before sampling kicks in
_ENTROPY_PROBES = 4             # sub-reads a sampled window is spread across


def file_entropy(path, windows=72, sample=_ENTROPY_SAMPLE):
    """Per-window entropy across a FILE, without reading it all in.

    Returns ``(values, size, sampled)`` -- bits/byte per window, the file size,
    and whether any window was sampled rather than read whole. That third value
    is not decoration: a sampled curve is an estimate, and a viewer that draws
    it identically to an exact one is stating a measurement it did not make.

    ``windowed_entropy`` stays for bytes you already hold. This exists because
    the entropy view is most wanted on the files least suited to being read
    into memory -- a disk image, a multi-gigabyte capture.

    A sampled window is probed at several points rather than only at its head.
    Head-only sampling is cheaper by one seek and systematically blind to the
    case the view exists to catch: a container followed by an appended payload
    puts the interesting bytes at the END of a window.
    """
    size = os.path.getsize(path)
    if size == 0 or windows <= 0:
        return [], size, False
    # Deliberately the SAME boundaries windowed_entropy uses, so an unsampled
    # run of this function is byte-identical to it. Two functions answering one
    # question is fine; two functions answering it differently is the drift
    # this codebase keeps finding.
    bounds = [(i * size // windows, max(i * size // windows + 1,
                                        (i + 1) * size // windows))
              for i in range(windows)]
    sampled = max(hi - lo for lo, hi in bounds) > sample
    out = []
    with open(path, "rb") as fh:
        for start, end in bounds:
            if start >= size:
                break
            end = min(end, size)
            if not sampled:
                fh.seek(start)
                seg = fh.read(end - start)
            else:
                per = max(1, sample // _ENTROPY_PROBES)
                stride = max(1, (end - start - per) // max(1, _ENTROPY_PROBES - 1))
                chunks = []
                for p in range(_ENTROPY_PROBES):
                    at = min(start + p * stride, max(start, end - per))
                    fh.seek(at)
                    chunks.append(fh.read(min(per, end - at)))
                seg = b"".join(chunks)
            if not seg:
                break
            out.append(entropy_from_counts(byte_counts(seg), len(seg)))
    return out, size, sampled


def _d2xy(side, d):
    """Hilbert curve: distance d -> (x, y) on a side x side grid (side = 2^k)."""
    x = y = 0
    t = d
    s = 1
    while s < side:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        if ry == 0:
            if rx == 1:
                x = s - 1 - x
                y = s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        t //= 4
        s *= 2
    return x, y


_HILBERT_PER_CELL = 64          # bytes sampled per cell when a file is too big


def hilbert_from_file(path, order=6, per_cell=_HILBERT_PER_CELL):
    """``hilbert_grid`` over a FILE, bounded, covering the whole of it.

    Returns ``(grid, side, sampled)``.

    The obvious bounded version reads the first N bytes and maps those, which
    turns a map of a 2 GB image into a map of its first fragment while the
    caption still names the full size. Reading a small sample per CELL instead
    keeps every cell anchored to the offset range it represents, so the picture
    still spans the file -- at a fixed cost of ``side^2 * per_cell`` bytes, or
    256 KB at order 6.

    Exact when the file is small enough that each cell's range fits in
    ``per_cell``; ``sampled`` says which happened.
    """
    side = 1 << order
    cells = side * side
    grid = [[None] * side for _ in range(side)]
    n = os.path.getsize(path)
    if n == 0:
        return grid, side, False
    sampled = False
    with open(path, "rb") as fh:
        for i in range(cells):
            lo = i * n // cells
            hi = max(lo + 1, (i + 1) * n // cells)
            want = min(per_cell, hi - lo)
            if hi - lo > want:
                sampled = True
            fh.seek(lo)
            chunk = fh.read(want)
            if not chunk:
                continue
            x, y = _d2xy(side, i)
            grid[y][x] = sum(chunk) // len(chunk)
    return grid, side, sampled


def hilbert_grid(data, order=5):
    """Lay bytes along a Hilbert space-filling curve into a 2^order square grid;
    each cell is the mean byte of its slice (or None). Adjacent file offsets stay
    spatially adjacent, so headers, PCM, and appended/cavity regions show up as
    distinct blocks."""
    side = 1 << order
    cells = side * side
    grid = [[None] * side for _ in range(side)]
    n = len(data)
    if n == 0:
        return grid, side
    for i in range(cells):
        lo = i * n // cells
        hi = max(lo + 1, (i + 1) * n // cells)
        chunk = data[lo:hi]
        if not chunk or lo >= n:
            continue
        x, y = _d2xy(side, i)
        grid[y][x] = sum(chunk) // len(chunk)
    return grid, side


# byte class -> (glyph for no-color terminals, hex color for color terminals)
def byte_class(b):
    """(glyph, class) for a byte's binvis class. class is a tui_theme.BYTE_CLASS
    key; color lives in the theme so core stays presentation-free."""
    if b is None:
        return " ", "empty"
    if b == 0x00:
        return ".", "null"
    if b == 0xFF:
        return "#", "ff"
    if 0x20 <= b <= 0x7E:
        return "o", "ascii"
    if b < 0x20:
        return "-", "ctrl"
    return "+", "high"
