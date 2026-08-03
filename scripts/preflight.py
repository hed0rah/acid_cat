"""Run the suite the way CI will, before pushing.

CI has nothing that git does not carry. This machine has a great deal more: a
2,327-file sample library, a 16 MB format-fixture tree, 731 MB of instrument
packs -- all gitignored, all invisible to a test that forgets to guard for
them. Every red build on this project so far came from that gap, not from a
real defect in the code:

    a test hardcoding /tmp/...           passes here, no such path on a runner
    a test using the local registry      passes here, empty registry on CI
    three TUI copyfile calls unguarded   never ran here, textual was absent

So this hides everything git does not track, and runs the suite against that.
If it passes here it will pass there.

    python scripts/preflight.py            # the CI-equivalent run
    python scripts/preflight.py --full     # then again with local corpora

Restores everything it moved, including on Ctrl-C and on failure.
"""

import argparse
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Gitignored paths the tests may reach for. CI has none of them, so a green run
# here with these hidden is the honest signal.
HIDDEN = [
    "data/test_formats",
    "test_samples",
]

# Env vars that point the suite at local corpora
CLEARED = ["ACIDCAT_CORPUS", "ACIDCAT_CORPUS_LIMIT", "ACIDCAT_DB",
           "ACIDCAT_REGISTRY"]


def _hide(paths):
    moved = []
    for rel in paths:
        src = os.path.join(REPO, rel)
        if os.path.exists(src):
            dst = src + ".preflight-hidden"
            shutil.move(src, dst)
            moved.append((src, dst))
    return moved


def _restore(moved):
    for src, dst in moved:
        if os.path.exists(dst):
            if os.path.exists(src):
                shutil.rmtree(src, ignore_errors=True)
            shutil.move(dst, src)


def run(full=False, extra_args=()):
    env = {k: v for k, v in os.environ.items() if k not in CLEARED}
    moved = [] if full else _hide(HIDDEN)
    if moved:
        print(f"hidden from the suite: {', '.join(rel for rel in HIDDEN)}")
    try:
        cmd = [sys.executable, "-m", "pytest", "-q", "-rs", *extra_args]
        print(f"$ {' '.join(cmd)}\n")
        return subprocess.call(cmd, cwd=REPO, env=env)
    finally:
        _restore(moved)
        if moved:
            print("\nrestored local corpora")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--full", action="store_true",
                    help="Do NOT hide local corpora -- the wide run this "
                         "machine can do and CI cannot.")
    ap.add_argument("pytest_args", nargs="*",
                    help="Passed through to pytest.")
    args = ap.parse_args(argv)

    rc = run(full=args.full, extra_args=args.pytest_args)
    if rc == 0:
        where = "with local corpora" if args.full else "as CI will see it"
        print(f"\nPASS {where}.")
    else:
        print(f"\nFAIL (exit {rc}). Do not push -- CI will fail the same way.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
