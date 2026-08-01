"""
acidcat CLI -- top-level argument parser and subcommand dispatcher.

Usage:
    acidcat file.wav                 # info for a single file (WAV, AIFF, MIDI, Serum)
    acidcat /path/to/samples         # scan a directory
    acidcat -                        # read from stdin
    cat file.wav | acidcat           # piped input (implicit stdin)
    acidcat info file.aif            # explicit info subcommand
    acidcat scan DIR [-n N]          # batch scan (writes CSV)
    acidcat chunks file.wav          # RIFF chunk walk
    acidcat survey DIR               # chunk type census
    acidcat detect file.wav          # librosa BPM/key estimation
    acidcat features DIR             # ML feature extraction
    acidcat dump file.wav acid       # hex dump a chunk
    acidcat carve file.wav --trailing -o blob   # extract a byte range / appended blob
    acidcat convert font.sf2                     # extract SoundFont samples to WAV
    acidcat index DIR                # upsert DIR into the global SQLite index
    acidcat query --bpm 120:130      # filter the global index
"""

import argparse
import errno
import os
import sys

from acidcat import __version__
from acidcat.commands._output import add_output_format_arg
from acidcat.commands import (
    info, scan, shape, od, chunks, survey, detect, features, similar, dump,
    index, query, inspect, convert, write, cover, explore, tui, carve, repair, validate, audit, probe,
    census, locate, extract, formats,
)
from acidcat.util.stdin import is_stdin_target

SUBCOMMANDS = {
    "info", "scan", "shape", "od", "chunks", "survey", "detect", "features",
    "similar", "dump", "index", "query", "inspect", "convert", "write", "cover",
    "explore", "tui", "carve", "repair", "validate", "audit", "probe", "locate", "extract",
    "formats",
}


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="acidcat",
        description="Audio metadata explorer and analysis tool.",
    )
    parser.add_argument("--version", action="version", version=f"acidcat {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    info.register(subparsers)
    scan.register(subparsers)
    shape.register(subparsers)
    od.register(subparsers)
    chunks.register(subparsers)
    survey.register(subparsers)
    detect.register(subparsers)
    features.register(subparsers)
    similar.register(subparsers)
    dump.register(subparsers)
    index.register(subparsers)
    query.register(subparsers)
    inspect.register(subparsers)
    convert.register(subparsers)
    write.register(subparsers)
    cover.register(subparsers)
    explore.register(subparsers)
    tui.register(subparsers)
    carve.register(subparsers)
    repair.register(subparsers)
    validate.register(subparsers)
    audit.register(subparsers)
    probe.register(subparsers)
    census.register(subparsers)
    locate.register(subparsers)
    extract.register(subparsers)
    formats.register(subparsers)

    # keep a handle to the subparser table so unrecognized arguments can be
    # reported against the chosen subcommand's usage, not the top-level one.
    parser._sub = subparsers
    return parser


def _try_bare_path(argv):
    """
    If the first non-flag arg is a path (not a subcommand), auto-route to
    info (file) or scan (directory).
    """
    if argv is None:
        argv = sys.argv[1:]

    # is the first positional arg a known subcommand?
    # note: "-" (stdin) starts with "-" but is a positional, not a flag
    positionals = [a for a in argv if not a.startswith("-") or a == "-"]
    if not positionals:
        return None
    first = positionals[0]
    if first in SUBCOMMANDS:
        return None  # let normal parsing handle it

    # not a subcommand -- is it a path?
    if os.path.exists(first) or is_stdin_target(first):
        # build a lightweight fallback parser that accepts the bare-path form
        fb = argparse.ArgumentParser(add_help=False)
        fb.add_argument("target")
        add_output_format_arg(fb, only=("table", "json", "csv"))
        fb.add_argument("-o", "--output", default=None)
        fb.add_argument("-q", "--quiet", action="store_true")
        fb.add_argument("-v", "--verbose", action="store_true")
        fb.add_argument("--deep", action="store_true")
        fb.add_argument("-n", "--num", type=int, default=500)
        fb.add_argument("--has", default=None)
        fb.add_argument("--fallback", action="store_true")
        fb.add_argument("--features", action="store_true")
        fb_args, _ = fb.parse_known_args(argv)

        if is_stdin_target(fb_args.target):
            return info.run(fb_args)
        elif os.path.isfile(fb_args.target):
            return info.run(fb_args)
        elif os.path.isdir(fb_args.target):
            return scan.run(fb_args)

    return None


def main(argv=None):
    """Entry point. Wraps the dispatch so a closed pipe is a normal exit.

    `acidcat od big.bin | head` closes stdout early; without this the
    interpreter reports BrokenPipeError on shutdown and exits non-zero, which
    makes every verb unsafe to pipe into a pager or `head`. Every other Unix
    tool treats it as "the reader left" and stops quietly, so we do too.
    """
    try:
        return _dispatch(argv)
    except OSError as e:
        if not _is_closed_pipe(e):
            raise
        # stop writing, and keep the interpreter's shutdown flush from raising
        # again on the dead descriptor
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 0
    except KeyboardInterrupt:
        print("\nacidcat: interrupted", file=sys.stderr)
        return 130                      # the shell's convention for SIGINT


def _is_closed_pipe(exc):
    """True when an OSError means "the reader went away".

    POSIX raises BrokenPipeError (EPIPE). Windows does not: writing to a pipe
    whose reader has closed surfaces as a plain OSError with EINVAL, or
    winerror 232 (ERROR_NO_DATA, "the pipe is being closed"). Matching only
    BrokenPipeError would leave `acidcat od big.bin | head` printing a
    traceback on Windows, which is where this tool mostly runs.
    """
    if isinstance(exc, BrokenPipeError):
        return True
    return (exc.errno in (errno.EPIPE, errno.EINVAL)
            or getattr(exc, "winerror", None) == 232)


def _dispatch(argv=None):
    # audio metadata is Unicode (UTF-8/UTF-16 tags), so emit UTF-8 regardless
    # of the platform default. Windows consoles and pipes default to cp1252 and
    # would raise UnicodeEncodeError on a non-Latin tag; replace stays a safety
    # net (all text encodes under UTF-8).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    # try bare-path dispatch first (before argparse can error on unknown subcommand)
    result = _try_bare_path(argv)
    if result is not None:
        return result

    # if no args and stdin is piped, read from stdin
    effective = argv if argv is not None else sys.argv[1:]
    if not effective and not sys.stdin.isatty():
        return _try_bare_path(["-"])

    parser = _build_parser()
    args, extras = parser.parse_known_args(argv)
    if extras:
        # an unrecognized flag or stray argument. if a valid subcommand was
        # named, print that subcommand's usage (readelf/git behavior) rather
        # than the top-level usage, which is what the user actually needs.
        cmd = getattr(args, "command", None)
        msg = "unrecognized arguments: " + " ".join(extras)
        if cmd and cmd in parser._sub.choices:
            parser._sub.choices[cmd].error(msg)
        parser.error(msg)

    if args.command is None:
        parser.print_help()
        return 1

    if hasattr(args, 'func'):
        return args.func(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
