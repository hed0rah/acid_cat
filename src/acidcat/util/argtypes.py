"""Shared argparse validators.

One rule, one implementation. The MCP layer grew a limit guard that rejects
negatives, documented the hazard in its own docstring, and the CLI verbs beside
it never got it -- so the same input was an error through one face of the tool
and silent data loss through the other.
"""

import argparse


def nonneg_int(value):
    """A whole number >= 0, for row limits and counts.

    SQLite reads ``LIMIT -1`` as unlimited, and Python reads ``rows[:-1]`` as
    all-but-the-last, so a negative limit fetched everything and then dropped
    one row. ``query --limit -1`` returned 381 of 382 matches: not an error,
    not the full set, and no indication either way. The single missing row is
    what makes it worth rejecting rather than clamping -- a wrong answer that
    looks like a right one.

    Zero is allowed and means zero. That is a real request ("just tell me the
    count"), distinct from a negative, which is never meaningful here.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}")
    if n < 0:
        raise argparse.ArgumentTypeError(
            f"must be zero or positive, got {n} (a negative limit silently "
            f"returned all-but-{abs(n)} rows)")
    return n
