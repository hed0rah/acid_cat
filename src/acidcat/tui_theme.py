"""Shared TUI brand palette -- the single source of truth for the colors both
acidcat's TUI and the acidcat-playground TUI set in code, so those cannot drift.

Brand: an ink canvas with a gunmetal-silver grayscale carrying the interface,
and two accents used sparingly -- kill-engn teal for structure/focus, rally
orange for attention (unsaved, danger, mutation). Import the palette constants
and the Rich helpers instead of hardcoding hex:

    from acidcat import tui_theme as th
    label = th.mark("data", th.TEAL, bold=True)
    color = th.byte_color(b)

The apps' Textual CSS blocks still spell the same hex literally (kept in sync by
hand for now; a shared Textual theme could source the CSS too, later).

Pure data + string helpers -- no Textual/Rich import, so importing this is free
and it stays usable from either repo.
"""

# ── themes ────────────────────────────────────────────────────────────
# the brand theme is the default. add alternates (a high-contrast variant, a
# more-colorful one) as new dict entries; the module-level constants below are
# sourced from DEFAULT_THEME, so a future switcher only re-sources them.
DEFAULT_THEME = "brand"

THEMES = {
    "brand": {
        # grayscale ramp: ink canvas -> gunmetal silver text
        "BG": "#16181C", "INSET": "#101217", "GUTTER": "#3A3E45",
        "DIM": "#565B63", "SOFT": "#8A9099", "FG": "#C9CDD3",
        # accents (sparing): kill-engn teal, rally orange, calmer amber
        "TEAL": "#08F9DF", "ORANGE": "#FF4D00", "AMBER": "#E0913E",
        # multi-item ramp (teal -> silver -> orange), restrained, no neon rainbow
        "PALETTE": ["#08F9DF", "#5CD9CE", "#93C9C2", "#C9CDD3",
                    "#D6B49E", "#E88F63", "#F56A31", "#FF4D00"],
        # byte-class colors for SOLID BLOCKS -- the Hilbert map and probe map,
        # where a cell is a filled glyph and "nulls recede into the ground" is
        # exactly right.
        "BYTE_CLASS": {"ascii": "#08F9DF", "high": "#FF4D00", "ctrl": "#8A9099",
                       "null": "#3A3E45", "ff": "#FF8A5C", "empty": "#16181C"},
        # the same classes for TEXT, where the digits still have to be read.
        # null at #3A3E45 measures 1.65:1 against the #16181C background: fine
        # behind a block, unreadable as "00" -- and 00 is the most common byte
        # in every header this points at. DIM is 2.60:1 and is already what the
        # ascii column uses for non-printables. `empty` has no meaning in a hex
        # dump (there is no such thing as an absent byte) so it is not here.
        "BYTE_CLASS_TEXT": {"ascii": "#08F9DF", "high": "#FF4D00",
                            "ctrl": "#8A9099", "null": "#565B63",
                            "ff": "#FF8A5C"},
    },

    # ── Kill ENGN ─────────────────────────────────────────────────────
    # The house baseline, from brand-tokens.css: teal primary with the cy_borg
    # heritage pair (PMS 806C pink, PMS 803C yellow) as the second and third
    # accents. Brighter and colder than `brand`, which is the same skeleton
    # wearing rally orange.
    "killengn": {
        "BG": "#14181B", "INSET": "#0F1316", "GUTTER": "#2D343A",
        "DIM": "#4E585E", "SOFT": "#6A747A", "FG": "#CDD6DA",
        "TEAL": "#00F9DF", "ORANGE": "#FF3EB5", "AMBER": "#FFE900",
        # teal -> steel -> borg pink, so a run of chunks reads as one sweep
        "PALETTE": ["#00F9DF", "#5FD9D2", "#93C4C6", "#CDD6DA",
                    "#D9A9C4", "#F074BE", "#FF3EB5", "#FFE900"],
        "BYTE_CLASS": {"ascii": "#00F9DF", "high": "#FF3EB5", "ctrl": "#6A747A",
                       "null": "#2D343A", "ff": "#FFE900", "empty": "#14181B"},
        "BYTE_CLASS_TEXT": {"ascii": "#00F9DF", "high": "#FF3EB5",
                            "ctrl": "#6A747A", "null": "#4E585E",
                            "ff": "#FFE900"},
    },

    # ── Fate Rally ────────────────────────────────────────────────────
    # teal + rally orange, the sub-brand pairing, on the same gunmetal.
    # Warmer than Kill ENGN and higher-contrast than `brand`.
    "faterally": {
        "BG": "#16181C", "INSET": "#101217", "GUTTER": "#313135",
        "DIM": "#5A5F67", "SOFT": "#8A9099", "FG": "#C9CDD3",
        "TEAL": "#00F9DF", "ORANGE": "#FF4D00", "AMBER": "#FFB000",
        "PALETTE": ["#00F9DF", "#63D8CB", "#9BC6BC", "#C9CDD3",
                    "#E0B394", "#F58A4F", "#FF6A1F", "#FF4D00"],
        "BYTE_CLASS": {"ascii": "#00F9DF", "high": "#FF4D00", "ctrl": "#8A9099",
                       "null": "#313135", "ff": "#FFB000", "empty": "#16181C"},
        "BYTE_CLASS_TEXT": {"ascii": "#00F9DF", "high": "#FF4D00",
                            "ctrl": "#8A9099", "null": "#5A5F67",
                            "ff": "#FFB000"},
    },
}

def _selected():
    """Which theme this process runs in.

    Read from the environment at import, because the module-level constants
    below are bound once and every consumer does `from tui_theme import ACCENT`
    -- rebinding them later would not reach a single one of those names. A
    switcher that changed them at runtime would appear to work and change
    nothing, which is worse than not having one.

    An unknown name falls back to the default and says so on stderr rather than
    starting in a theme nobody asked for.
    """
    import os
    import sys
    want = (os.environ.get("ACIDCAT_THEME") or "").strip().lower()
    if not want:
        return DEFAULT_THEME
    if want in THEMES:
        return want
    print(f"acidcat: unknown theme {want!r}; using {DEFAULT_THEME!r}. "
          f"available: {', '.join(sorted(THEMES))}", file=sys.stderr)
    return DEFAULT_THEME


ACTIVE_THEME = _selected()
_T = THEMES[ACTIVE_THEME]
BG = _T["BG"]
INSET = _T["INSET"]
GUTTER = _T["GUTTER"]
DIM = _T["DIM"]
SOFT = _T["SOFT"]
FG = _T["FG"]
TEAL = _T["TEAL"]
ORANGE = _T["ORANGE"]
AMBER = _T["AMBER"]
PALETTE = _T["PALETTE"]
BYTE_CLASS = _T["BYTE_CLASS"]
BYTE_CLASS_TEXT = _T["BYTE_CLASS_TEXT"]

# semantic aliases (name the role, not the color, at the call site)
ACCENT = TEAL        # navigation / structure / focus
PEND = ORANGE        # pending / unsaved / live edit preview
ALERT = ORANGE       # danger / forensics

# ── severity -> color ─────────────────────────────────────────────────
SEV = {"alert": ORANGE, "warn": AMBER, "notice": TEAL, "info": DIM}


# ── helpers ───────────────────────────────────────────────────────────
def chunk_color(i):
    """Stable brand color for the i-th chunk/region (cycles the ramp)."""
    return PALETTE[i % len(PALETTE)]


def ramp_color(t):
    """The PALETTE ramp sampled continuously at `t` in 0..1 -> '#rrggbb'.

    PALETTE is eight stops, which is enough to tell categories apart and not
    enough to read a magnitude off: an entropy chart quantised to eight colours
    shows a broad teal-to-orange sweep and nothing about the differences within
    it. Interpolating between the same stops keeps the brand ramp exactly as it
    is and gives a bar's colour back its resolution.
    """
    t = 0.0 if t != t else max(0.0, min(1.0, float(t)))   # t != t catches NaN
    pos = t * (len(PALETTE) - 1)
    i = min(len(PALETTE) - 2, int(pos))
    f = pos - i
    a, b = PALETTE[i].lstrip("#"), PALETTE[i + 1].lstrip("#")
    out = []
    for c in (0, 2, 4):
        lo, hi = int(a[c:c + 2], 16), int(b[c:c + 2], 16)
        out.append(round(lo + (hi - lo) * f))
    return "#%02x%02x%02x" % tuple(out)


def css(template):
    """Fill $NAME placeholders in a Textual CSS block from the active theme.

    string.Template rather than an f-string or .format(): CSS rule bodies are
    written in braces, so both of those try to read `{ background: ... }` as a
    replacement field and fail on every rule in the file. Only `$` is special
    here, and CSS has no other use for it.

    Called at class-definition time, so the stylesheet is built once from
    whichever theme this process selected.
    """
    from string import Template
    return Template(template).substitute(
        BG=BG, INSET=INSET, GUTTER=GUTTER, DIM=DIM, SOFT=SOFT, FG=FG,
        TEAL=TEAL, ORANGE=ORANGE, AMBER=AMBER,
        ACCENT=ACCENT, PEND=PEND, ALERT=ALERT,
    )


def sev_color(level):
    """Color for a severity level; falls back to FG."""
    return SEV.get(level, FG)


def mark(text, color, bold=False):
    """Rich-markup a string: mark('data', TEAL, bold=True) -> '[b #08F9DF]data[/]'."""
    tag = ("b " if bold else "") + color
    return f"[{tag}]{text}[/]"


def byte_color(b):
    """Brand hex for a byte's binvis class -- composes core.viz.byte_class with
    BYTE_CLASS. Lazy-imports viz so importing the theme stays cheap."""
    from acidcat.core.forensics import viz
    return BYTE_CLASS[viz.byte_class(b)[1]]


