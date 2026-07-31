"""optional dependency helpers."""

import importlib.util
import sys


def available(*packages):
    """True when every package is importable. Silent, and does not import them
    (find_spec avoids paying librosa/numba's cold start just to probe)."""
    return all(importlib.util.find_spec(p) is not None for p in packages)


def require(*packages, group="analysis"):
    """Check that optional packages are importable.

    Returns True if all are available, False after printing
    an install hint to stderr.
    """
    missing = []
    for pkg in packages:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(
            f"acidcat: missing {', '.join(missing)} "
            f"-- install with: pip install acidcat[{group}]",
            file=sys.stderr,
        )
        return False
    return True
