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


def _sample_columns(values, dot_w):
    """`dot_w` values representing `values`, peak-preserving.

    Point-sampling here dropped data outright: a 256-bin byte histogram drawn
    across 69 cells has 138 dot columns, so 118 of the 256 bins were never
    looked at. A lone tall bar -- the 0x00 spike of a padded bank, the one
    hot window in a large file, exactly what these views exist to surface --
    could land on a skipped index and simply not be drawn, with a full-looking
    chart giving no sign anything was missing.

    So when there is more data than room, each output column reports the
    MAXIMUM over the range it covers. Nothing that would have been visible
    disappears; a peak may be one column wide instead of sub-pixel. When the
    data fits, the old interpolating index is kept, since a line plot of a few
    values should stay smooth rather than turn into steps.
    """
    n = len(values)
    if n == 0 or dot_w < 1:
        return []
    if n <= dot_w:
        return [values[min(n - 1,
                           int(round(x * (n - 1) / (dot_w - 1)))
                           if dot_w > 1 and n > 1 else 0)]
                for x in range(dot_w)]
    out = []
    for x in range(dot_w):
        lo = x * n // dot_w
        hi = max(lo + 1, (x + 1) * n // dot_w)
        out.append(max(values[lo:hi]))
    return out


def braille_line(values, width=72, height=8, vmin=None, vmax=None, fill=False):
    """Braille line (or filled area) plot, `height` strings top-first."""
    if not values or width < 1 or height < 1:
        return [" " * max(1, width) for _ in range(max(1, height))]
    dot_w, dot_h = width * 2, height * 4
    vmin = min(values) if vmin is None else vmin
    vmax = max(values) if vmax is None else vmax
    span = (vmax - vmin) or 1.0
    cols = _sample_columns(values, dot_w)
    dots = set()
    prev = None
    for x in range(dot_w):
        v = cols[x]
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


def column_peaks(values, width):
    """The value each output CELL stands for -- the taller of its two dot
    columns, taken from the same sampling braille_line draws from.

    Exists so a caller can colour a chart by bar height without guessing at
    the sampling. Deriving it independently is how the picture and its colours
    end up describing two different datasets.
    """
    if not values or width < 1:
        return []
    cols = _sample_columns(values, width * 2)
    return [max(cols[cx * 2], cols[cx * 2 + 1]) for cx in range(width)]


# vertical-axis mappings. A chart is only readable when its axis suits the
# data, and only honest when it says which axis it used -- an autoscaled
# entropy plot where 7.90 sits on the floor and 7.95 on the ceiling looks
# exactly like 0 versus 8 unless the caption gives the range away.
SCALES = ("absolute", "auto", "log", "clip")


def scale_values(values, mode="absolute", floor=0.0, ceiling=None, clip=0.99):
    """Map `values` onto 0..1 for drawing.

    Returns ``(norm, lo, hi, label)``. `label` names the axis actually used and
    is meant to be printed; callers should not invent their own wording.

      absolute  floor..ceiling as given (an axis that does not move)
      auto      min..max of the data (small differences become visible)
      log       log1p, floor..max (a dominant bin stops flattening the rest)
      clip      floor..the `clip` quantile, taller bars clipped and counted

    `auto` on flat data collapses to a zero span; it is reported as such rather
    than drawn as a full-height block, since "everything is identical" and
    "everything is at maximum" are different findings.
    """
    if not values:
        return [], 0.0, 0.0, mode
    lo = float(floor if floor is not None else min(values))
    hi = float(ceiling) if ceiling is not None else float(max(values))

    if mode == "auto":
        lo, hi = float(min(values)), float(max(values))
        if hi - lo < 1e-9:
            return [0.0] * len(values), lo, hi, f"auto (flat at {lo:.4g})"
        label = f"auto {lo:.4g}-{hi:.4g}"
    elif mode == "log":
        import math
        vals = [math.log1p(max(0.0, v - lo)) for v in values]
        top = max(vals) or 1.0
        return ([v / top for v in vals], lo, hi, "log")
    elif mode == "clip":
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(clip * (len(ordered) - 1)))
        hi = float(ordered[idx]) or float(max(values))
        over = sum(1 for v in values if v > hi)
        label = (f"clipped at {hi:.4g}"
                 + (f" ({over} above)" if over else ""))
    else:
        label = f"{lo:.4g}-{hi:.4g}"

    span = (hi - lo) or 1.0
    return ([max(0.0, min(1.0, (v - lo) / span)) for v in values],
            lo, hi, label)


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


def _span(path, start, end):
    """Clamp a caller's (start, end) to the file. Returns (start, end, length).

    One place, because an off-by-one here silently shifts every window in the
    picture and nothing raises.
    """
    size = os.path.getsize(path)
    start = max(0, min(int(start or 0), size))
    end = size if end is None else max(start, min(int(end), size))
    return start, end, end - start


def file_entropy(path, windows=72, sample=_ENTROPY_SAMPLE, start=0, end=None):
    """Per-window entropy across a FILE, without reading it all in.

    Returns ``(values, size, sampled)`` -- bits/byte per window, the number of
    bytes covered, and whether any window was sampled rather than read whole.
    That third value is not decoration: a sampled curve is an estimate, and a
    viewer that draws it identically to an exact one is stating a measurement
    it did not make.

    `start`/`end` narrow it to one byte range, which is what lets a caller plot
    a single chunk instead of the file it sits in. `size` is then the length of
    that range, so the return stays "how much this picture covers".

    ``windowed_entropy`` stays for bytes you already hold. This exists because
    the entropy view is most wanted on the files least suited to being read
    into memory -- a disk image, a multi-gigabyte capture.

    A sampled window is probed at several points rather than only at its head.
    Head-only sampling is cheaper by one seek and systematically blind to the
    case the view exists to catch: a container followed by an appended payload
    puts the interesting bytes at the END of a window.
    """
    base, stop, size = _span(path, start, end)
    if size == 0 or windows <= 0:
        return [], size, False
    # Deliberately the SAME boundaries windowed_entropy uses, so an unsampled
    # run of this function is byte-identical to it. Two functions answering one
    # question is fine; two functions answering it differently is the drift
    # this codebase keeps finding.
    bounds = [(base + i * size // windows,
               base + max(i * size // windows + 1, (i + 1) * size // windows))
              for i in range(windows)]
    sampled = max(hi - lo for lo, hi in bounds) > sample
    out = []
    with open(path, "rb") as fh:
        for w_lo, w_hi in bounds:
            if w_lo >= stop:
                break
            w_hi = min(w_hi, stop)
            if not sampled:
                fh.seek(w_lo)
                seg = fh.read(w_hi - w_lo)
            else:
                per = max(1, sample // _ENTROPY_PROBES)
                stride = max(1, (w_hi - w_lo - per) // max(1, _ENTROPY_PROBES - 1))
                chunks = []
                for p in range(_ENTROPY_PROBES):
                    at = min(w_lo + p * stride, max(w_lo, w_hi - per))
                    fh.seek(at)
                    chunks.append(fh.read(min(per, w_hi - at)))
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


def hilbert_from_file(path, order=6, per_cell=_HILBERT_PER_CELL,
                      start=0, end=None):
    """``hilbert_grid`` over a FILE, bounded, covering the whole of it.

    Returns ``(grid, side, sampled)``.

    The obvious bounded version reads the first N bytes and maps those, which
    turns a map of a 2 GB image into a map of its first fragment while the
    caption still names the full size. Reading a small sample per CELL instead
    keeps every cell anchored to the offset range it represents, so the picture
    still spans the file -- at a fixed cost of ``side^2 * per_cell`` bytes, or
    256 KB at order 6.

    Exact when the file is small enough that each cell's range fits in
    ``per_cell``; ``sampled`` says which happened. `start`/`end` map one byte
    range instead of the whole file.
    """
    side = 1 << order
    cells = side * side
    grid = [[None] * side for _ in range(side)]
    base, _stop, n = _span(path, start, end)
    if n == 0:
        return grid, side, False
    sampled = False
    with open(path, "rb") as fh:
        for i in range(cells):
            lo = base + i * n // cells
            hi = base + max(i * n // cells + 1, (i + 1) * n // cells)
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
