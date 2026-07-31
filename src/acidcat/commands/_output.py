"""Shared CLI wiring for the output-format axis -- one definition, every command.

acidcat reserves three words so a flag never means two things:
  * **format**        a file's container/codec (WAV, FLAC, MP3) -- ``--format`` /
                      ``-f`` where a command filters or forces the input type.
  * **output**        where bytes go: ``-o`` / ``--output FILE`` (default stdout).
  * **output-format** how records are rendered: ``--output-format`` + the
                      ``--json`` / ``--csv`` shorthands, choices from the
                      render registry so they never drift.

``-f`` was historically the short output-rendering flag on many commands; it
still works but warns. ``--format`` (long) is reserved for the file-format axis
and never selects rendering. Add the standard flags with
``add_output_format_arg(parser)``.
"""

import argparse
import sys

from acidcat.core.infra import render


class _DeprecatedOutputFormat(argparse.Action):
    """Back-compat for the old -f/--format output flag: still sets the rendering
    but warns, since --format now means the file's format."""

    def __call__(self, parser, namespace, values, option_string=None):
        sys.stderr.write(
            f"acidcat: warning: {option_string} is deprecated for output "
            f"rendering; use --output-format {values} (or --json / --csv). "
            f"--format now selects the file format.\n")
        setattr(namespace, self.dest, values)


def add_output_format_arg(parser, default="table", only=None, deprecated_f=True):
    """Add the standard output-rendering flags to ``parser``.

    Adds ``--output-format`` (choices from the render registry, or the ``only``
    subset) plus ``--json`` / ``--csv`` shorthands when those formats are
    allowed. With ``deprecated_f`` (default), also accepts the old ``-f`` /
    ``--format`` spelling with a deprecation warning. The chosen format lands in
    ``args.output_format``; pass it to ``render.output(data, fmt=...)``.
    """
    choices = list(only) if only else list(render.output_formats())
    parser.add_argument(
        "--output-format", dest="output_format", default=default,
        choices=choices, metavar="FMT",
        help=f"Output rendering: {', '.join(choices)} (default: {default}).")
    if "json" in choices:
        parser.add_argument(
            "--json", dest="output_format", action="store_const", const="json",
            help="Render as JSON (shorthand for --output-format json).")
    if "csv" in choices:
        parser.add_argument(
            "--csv", dest="output_format", action="store_const", const="csv",
            help="Render as CSV (shorthand for --output-format csv).")
    if deprecated_f:
        # -f (short only) is the transitional bridge for the old output spelling.
        # --format is deliberately NOT added: it belongs to the file-format axis.
        parser.add_argument(
            "-f", dest="output_format", action=_DeprecatedOutputFormat,
            choices=choices, metavar="FMT", help=argparse.SUPPRESS)
    return parser
