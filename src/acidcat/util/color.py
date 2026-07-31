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
