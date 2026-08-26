"""The `acidcat-lab` entry point.

Deliberately a separate binary rather than a subcommand of `acidcat`. Someone
reading `acidcat --help` on a machine where the extra is not installed should
see no trace of construction tooling, and a subcommand that appears and
disappears depending on an extra is a worse interface than two names.
"""

import argparse
import sys

from acidcat_lab import __version__


def build_parser():
    ap = argparse.ArgumentParser(
        prog="acidcat-lab",
        description="Construct files that test what acidcat can see.")
    ap.add_argument("--version", action="version",
                    version="acidcat-lab %s" % __version__)
    ap.add_subparsers(dest="verb", metavar="VERB")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.verb:
        ap.print_help()
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
