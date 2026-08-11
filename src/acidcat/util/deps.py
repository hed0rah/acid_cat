"""optional dependency helpers."""

import importlib.util
import sys


def available(*packages):
    """True when every package is importable. Silent, and does not import them
    (find_spec avoids paying librosa/numba's cold start just to probe)."""
    return all(importlib.util.find_spec(p) is not None for p in packages)


# hints already emitted this process, so a multi-path verb says it once
_ANNOUNCED = set()


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
        # Once per process, per group. `detect` reaches this through more than
        # one path in a single run, so a user with no analysis stack was told
        # the same thing two and three times before being handed a table of
        # nulls -- it read as the tool scolding them rather than helping.
        key = (group, tuple(sorted(missing)))
        if key not in _ANNOUNCED:
            _ANNOUNCED.add(key)
            print(
                f"acidcat: missing {', '.join(missing)} "
                f"-- install with: pip install acidcat[{group}]",
                file=sys.stderr,
            )
        return False
    return True
