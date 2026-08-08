"""acidcat TUI -- byte/field rendering helpers and metadata edit profiles.

Pure helpers shared by the TUI screens and the app: hex rendering (hex_text,
_hex_rows), a fuzzy matcher (_fuzzy), bounded file reads (_read), and the
per-format metadata edit profiles (edit_profile, text_field_for) the edit form
is built from -- plus the view/scan/undo cap constants. No Textual app state.
"""

import os
import re

from rich.text import Text

from acidcat.tui_theme import DIM, FG, GUTTER, PALETTE


_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # braille scan spinner
_BAR_W = 18            # width of the scan progress bar

_HEX_CAP = 1024        # most bytes to render in the hex pane for one node
_ROW_CAP = 400         # most per-element rows (events/frames) to list per chunk
_HEXEDIT_CAP = 512     # refuse editing a byte region bigger than this (pick a field)
_VIZ_READ = 8 * 1024 * 1024   # bytes the histogram reads; the other views stream
_UNDO_CAP = 50         # most undo deltas to keep
_UNDO_BYTES_CAP = 64 * 1024 * 1024   # total delta bytes kept (latest always kept)
_DIFF_CAP = 200        # most changed regions to list in the pending-changes view
_LARGE_FILE = 64 * 1024 * 1024       # above this, browse in place (no working copy)
_SCAN_SEG = 16 * 1024 * 1024         # scan a blob in segments: live progress + cancel


def _read(path, off, length):
    try:
        with open(path, "rb") as f:
            f.seek(off)
            return f.read(length)
    except OSError:
        return b""


def _fuzzy(query, text):
    """fzf-style subsequence match: every char of `query` appears in `text`, in
    order, case-insensitively. The default TUI search over field names/values."""
    q, t = query.lower(), text.lower()
    i = 0
    for ch in t:
        if i < len(q) and ch == q[i]:
            i += 1
    return i == len(q)


# A row is gutter + hex cells + the mid gap + the ascii column. Powers of two
# only: at 16 or 8 per row a column still maps to the low nibble of the offset,
# which is most of why a hex grid is readable at all.
_ROW_WIDTHS = (16, 8, 4)


def row_width_for(columns):
    """Bytes per row that fit in ``columns``, largest first. 16 needs 76.

    The grid folds rather than scrolling horizontally, so a row wider than the
    pane does not merely look cramped -- it wraps, and column position stops
    meaning anything.
    """
    for n in _ROW_WIDTHS:
        if columns >= 10 + 3 * n + (1 if n > 8 else 0) + 1 + n:
            return n
    return _ROW_WIDTHS[-1]


def hex_text(path, off, length, accent, spans=None, width=16):
    """A colored hex dump (offset gutter + hex columns + ascii) of up to
    _HEX_CAP bytes starting at off. Bytes render in `accent`; when `spans` (a
    list of (abs_offset, len) field ranges) is given, each field's bytes take a
    distinct palette color so a chunk's field structure shows in the hex.
    Non-printable ascii dims out."""
    t = Text()
    if off is None or length in (None, 0):
        t.append("  (no byte range for this node)", style=DIM)
        return t
    shown = min(length, _HEX_CAP)
    raw = _read(path, off, shown)
    _hex_rows(t, off, raw, accent,
              _spans_cmap(off, spans, shown) if spans else None, width)
    if length > shown:
        t.append(f"  .. {length - shown:,} more bytes\n", style=DIM)
    return t


def _spans_cmap(base_off, spans, limit):
    """Map each shown byte position (relative to base_off) to a per-field color,
    cycling the palette across the fields."""
    cmap = {}
    for i, (ao, ln) in enumerate(spans):
        color = PALETTE[i % len(PALETTE)]
        start = ao - base_off
        for p in range(max(0, start), min(limit, start + ln)):
            cmap[p] = color
    return cmap


def _hex_rows(t, off, raw, byte_style, cmap=None, width=16):
    """Append hex-dump rows (gutter + hex + ascii) for `raw` to Text `t`.

    `cmap` maps a position (relative to `off`) to a style, and it applies to
    BOTH columns. The ascii column used to be styled independently, which meant
    a caller could not put one style on a byte -- and that gap is the only
    reason the hex editor had to hand-inline its own copy of this loop to get a
    cursor onto both halves of a row.
    """
    for row in range(0, len(raw), width):
        chunk = raw[row:row + width]
        t.append(f"{off + row:08x}  ", style=GUTTER)
        for i in range(width):
            if i < len(chunk):
                style = cmap.get(row + i, byte_style) if cmap else byte_style
                t.append(f"{chunk[i]:02x} ", style=style)
            else:
                t.append("   ")
            if width > 8 and i == (width // 2) - 1:
                t.append(" ")
        t.append(" ")
        for i, b in enumerate(chunk):
            printable = 32 <= b < 127
            # the cmap wins; otherwise printable/not is the only signal here
            style = (cmap or {}).get(row + i) or (FG if printable else DIM)
            t.append(chr(b) if printable else ".", style=style)
        t.append("\n")


# editable-field profiles, mirroring what the write engine accepts per format.
# (field, label) -- field is the --set name commands.write understands.
_WAV_FIELDS = [("title", "title"), ("artist", "artist"), ("album", "album"),
               ("genre", "genre"), ("comment", "comment"), ("date", "date"),
               ("bpm", "bpm"), ("key", "key"),
               ("root_note", "root note (C3 or 60)")]
_AIFF_FIELDS = [("title", "title"), ("artist", "artist"), ("comment", "comment")]
_TAGGED_FIELDS = [("title", "title"), ("artist", "artist"), ("album", "album"),
                  ("genre", "genre"), ("comment", "comment"), ("date", "date"),
                  ("bpm", "bpm"), ("key", "key")]
_VITAL_FIELDS = [("name", "preset name"), ("author", "author"),
                 ("comment", "comments")]


def edit_profile(path):
    """Return (profile_name, [(field, label), ...]) for the file's format, or
    None where the write engine has no editor (or editing is disabled, e.g.
    Bitwig/NI). Routing mirrors commands.write._edit so the form only offers
    fields a save can actually apply."""
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        head = f.read(16)
    if ext == ".vital" or head[:1] == b"{":
        return ("Vital", _VITAL_FIELDS)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ("WAV", _WAV_FIELDS)
    # Bitwig / NI preset writing is disabled in the engine; do not offer it.
    if (head[:4] == b"BtWg" or head[12:16] == b"hsin" or head[:4] == b"-in-"
            or (head[:4] == b"RIFF" and head[8:12] == b"NIKS")):
        return None
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return ("AIFF", _AIFF_FIELDS)
    tagged = (head[:4] == b"fLaC" or head[:3] == b"ID3" or head[:4] == b"OggS"
              or head[4:8] == b"ftyp"
              or ext in (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4"))
    if tagged:
        return ("tagged", _TAGGED_FIELDS)
    return None




# tagged-audio text fields the write engine (mutagen) can set, keyed by the
# walker's field name: ID3 frame ids (mp3) and Vorbis comment keys (flac/ogg).
_ID3_TEXT = {"TIT2": "title", "TPE1": "artist", "TALB": "album", "TCON": "genre",
             "COMM": "comment", "TDRC": "date", "TYER": "date", "TBPM": "bpm",
             "TKEY": "key", "TRCK": "track"}
_VORBIS_TEXT = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album",
                "GENRE": "genre", "COMMENT": "comment", "DESCRIPTION": "comment",
                "DATE": "date", "BPM": "bpm", "KEY": "key", "INITIALKEY": "key",
                "TRACKNUMBER": "track"}


def text_field_for(profile, field_name):
    """If `field_name` (a walker field name) is a variable-length text field the
    write engine can edit, return the engine field name to route it through;
    else None. These must NOT be same-length byte-patched -- a longer title
    shifts the file -- so the editor re-serializes via the metadata engine."""
    if profile == "WAV":
        from acidcat.core.write.edit_riff import _INFO_TAGS
        rev = {v.decode("latin1").strip(): k for k, v in _INFO_TAGS.items()}
        return rev.get(field_name)
    if profile == "AIFF":
        from acidcat.core.write.edit_aiff import _AIFF_TEXT
        rev = {v.decode("latin1").strip(): k for k, v in _AIFF_TEXT.items()}
        return rev.get(field_name)
    if profile == "tagged":
        n = field_name.strip()
        return _ID3_TEXT.get(n) or _VORBIS_TEXT.get(n.upper())
    return None




_SIZE_ECHO = re.compile(r",?\s*\b[\d,]+ bytes\b")


def trim_size_echo(summary, size):
    """Drop a byte count from a chunk summary when the row already shows it.

    The tree row prints the size itself, then the walker's summary prints it
    again -- "data 0x46 176,400b audio payload, 176,400 bytes, 1.000 s". Two
    statements of one fact, and on the widest chunks it is what pushed the row
    past the pane. Only an exact match is removed: a summary quoting a
    *different* number is saying something (a declared size, a payload inside a
    larger chunk) and must survive untouched.
    """
    text = str(summary or "")
    if not size:
        return text
    def drop(m):
        try:
            return "" if int(m.group(0).replace(",", "").replace("bytes", "").strip()) == size else m.group(0)
        except ValueError:
            return m.group(0)
    out = _SIZE_ECHO.sub(drop, text)
    return re.sub(r"\s{2,}", " ", out).strip().strip(",").strip()
