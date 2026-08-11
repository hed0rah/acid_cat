"""Shared --color handling -- one spelling of the TTY/ANSI axis for every command.

``--color=auto|always|never`` (the near-universal convention), auto meaning
"color only when stdout is a TTY". ``never`` and ``always`` are explicit
overrides; ``auto`` additionally honours the ``NO_COLOR`` env var. Add it with
``add_color_arg(parser)`` and gate ANSI on ``color_enabled(args)``.
"""

import argparse
import os
import sys


class _NoColorAlias(argparse.Action):
    """Back-compat for the old boolean --no-color: sets --color never, warns."""

    def __call__(self, parser, namespace, values, option_string=None):
        sys.stderr.write("acidcat: warning: --no-color is deprecated; "
                         "use --color never\n")
        setattr(namespace, "color", "never")


def add_color_arg(parser, deprecated_no_color=False):
    """Add ``--color {auto,always,never}`` (default auto) to ``parser``.

    With ``deprecated_no_color``, also accept the old ``--no-color`` boolean as a
    hidden alias for ``--color never`` (with a deprecation warning).
    """
    parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto",
        help="Colorize output: auto (only on a TTY), always, or never. "
             "auto honours NO_COLOR.")
    if deprecated_no_color:
        parser.add_argument(
            "--no-color", dest="color", nargs=0, action=_NoColorAlias,
            help=argparse.SUPPRESS)
    return parser


def color_enabled(args):
    """Whether to emit ANSI color, per ``args.color`` (default auto)."""
    mode = getattr(args, "color", "auto")
    if mode == "never":
        return False
    if mode == "always":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def fg(hexcolor, text):
    """Wrap ``text`` in a 24-bit foreground colour from a ``#rrggbb`` string.

    The theme speaks hex because that is what Rich and the HTML explorer want;
    a terminal wants an SGR escape. This is the bridge, and it lives here
    because this module already owns the ANSI axis. Callers gate it on
    ``color_enabled(args)`` -- it does not check, so a caller cannot forget
    that it is a formatting decision rather than a colour one.
    """
    r, g, b = int(hexcolor[1:3], 16), int(hexcolor[3:5], 16), int(hexcolor[5:7], 16)
    return f"\x1b[38;2;{r};{g};{b}m{text}\x1b[0m"


def bg(hexcolor, text):
    """As ``fg``, on the background. Kept as a separate channel on purpose:
    foreground already carries field identity in both hex renderers, so an
    overlay that wants its own channel has to use this one."""
    r, g, b = int(hexcolor[1:3], 16), int(hexcolor[3:5], 16), int(hexcolor[5:7], 16)
    return f"\x1b[48;2;{r};{g};{b}m{text}\x1b[0m"
