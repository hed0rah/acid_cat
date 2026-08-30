"""
acidcat CLI -- top-level argument parser and subcommand dispatcher.

Usage:
    acidcat file.wav                 # info for a single file (see `acidcat formats`)
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
import traceback

from acidcat import __version__
from acidcat.commands._output import add_output_format_arg
from acidcat.commands import (
    info, scan, shape, od, chunks, survey, detect, features, similar, dump,
    classify,
    wrap,
    index, query, inspect, convert, write, cover, explore, tui, carve, repair, validate, audit, probe,
    census, locate, extract, formats,
)
from acidcat.util.stdin import is_stdin_target

# Filled from the parser once it is built. It used to be a hand-maintained
# literal, which drifts the moment a verb is added: `census` and `wrap` were
# both missing, so a directory of either name in the cwd shadowed the command
# and `acidcat wrap ...` silently ran `scan` on the directory instead.
SUBCOMMANDS = set()


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
    classify.register(subparsers)
    wrap.register(subparsers)

    # keep a handle to the subparser table so unrecognized arguments can be
    # reported against the chosen subcommand's usage, not the top-level one.
    parser._sub = subparsers
    # derive the shadow-guard set from the parser itself, so adding a verb can
    # never again leave it out
    SUBCOMMANDS.update(subparsers.choices)
    return parser


def _scan_default_format():
    """`scan`'s own default rendering, read from its parser rather than copied.

    Hard-coding "csv" here would just recreate the drift this exists to fix.
    """
    import argparse as _ap
    from acidcat.commands import scan as _scan
    p = _ap.ArgumentParser()
    sub = p.add_subparsers()
    _scan.register(sub)
    for act in sub.choices["scan"]._actions:
        if act.dest == "output_format" and act.default:
            return act.default
    return "csv"


def _try_bare_path(argv):
    """
    If the first non-flag arg is a path (not a subcommand), auto-route to
    info (file) or scan (directory).
    """
    if argv is None:
        argv = sys.argv[1:]

    # is the first positional arg a known subcommand?
    # note: "-" (stdin) starts with "-" but is a positional, not a flag
    if not SUBCOMMANDS:                 # populate on first use
        _build_parser()
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
            # This fallback parser is a SECOND declaration of flags the real
            # verbs already declare, and the two drifted: it defaults
            # output_format to "table" while `scan`'s own parser defaults to
            # "csv". So `acidcat DIR` and `acidcat scan DIR` -- which the README
            # presents as the same thing ("auto-detected") -- rendered
            # completely differently, and the bare form emitted a twelve-line
            # vertical record per file. Pointed at a 3,200-file library that is
            # roughly 38,000 lines into the terminal.
            #
            # Only override when the user did not ASK for a rendering, so an
            # explicit `acidcat DIR --json` still means what it says.
            asked = any(a == "--output-format" or a.startswith("--output-format=")
                        or a in ("--json", "--csv", "-f") for a in argv)
            if not asked:
                fb_args.output_format = _scan_default_format()
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
            # NOT a bare re-raise. `raise` here leaves main() entirely -- the
            # BaseException handler below is a sibling of this one, not an outer
            # net, so it never sees it. Every OSError from a parser therefore
            # kept printing a traceback and exiting 1, the code reserved for
            # "ran fine, and the answer is no", which is the exact hole the
            # handler below was added to close.
            traceback.print_exc()
            print(f"acidcat: {e.__class__.__name__}: {e}", file=sys.stderr)
            return 2
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
    except SystemExit:
        raise                           # argparse's own exits are deliberate
    except BaseException:
        # An unhandled exception used to propagate, print a traceback, and let
        # the interpreter exit 1 -- the same code the exit-code contract gives
        # to "ran fine, and the answer is no". So `validate f && ship f` read a
        # crash as a clean negative, and `audit f || quarantine f` quarantined
        # on a bug. grep and diff, the tools that convention cites, both use 2
        # for "could not run", and that is what a crash is.
        #
        # The traceback still prints: this changes what the shell is told, not
        # what the developer sees.
        traceback.print_exc()
        print("acidcat: internal error (this is a bug); exiting 2",
              file=sys.stderr)
        return 2


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
    # EINVAL is the Windows spelling of a dead pipe, but it is ALSO what an
    # invalid output filename raises -- and treating that as "the reader left"
    # made a failed `carve -o` exit 0 having written nothing. A failed file
    # operation carries the path in .filename; a write to a broken stdout does
    # not, so that is the discriminator.
    if exc.filename is not None:
        return False
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
