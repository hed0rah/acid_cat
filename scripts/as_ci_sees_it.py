"""Run the suite against what a CLONE contains, not what this machine does.

A local green is not evidence about the repo. `data/test_formats/` is gitignored
and 16 MB, so the suite here and the suite on a runner are different suites, and
for a long time nobody could see the difference from either side. That gap
shipped a release whose CI was red on all five platforms while the local run
reported 2,826 passing.

This extracts HEAD (or any commit) to a temporary directory, so the only files
present are the ones a clone would have, and runs pytest there.

    python scripts/as_ci_sees_it.py                  # HEAD
    python scripts/as_ci_sees_it.py --rev HEAD~3
    python scripts/as_ci_sees_it.py -- -k geometry   # args after -- go to pytest

Note the remaining differences, which this does NOT model: the runner's Python
version, its OS, and whether ffmpeg is installed. It answers one question --
"does this depend on a file only I have?" -- which is the question that has
actually cost releases.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rev", default="HEAD", help="commit to extract (default HEAD)")
    ap.add_argument("--keep", action="store_true",
                    help="leave the extracted tree in place and print its path")
    ap.add_argument("pytest_args", nargs="*",
                    help="passed through to pytest (put them after --)")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = tempfile.mkdtemp(prefix="acidcat-as-ci-")
    try:
        archive = subprocess.run(["git", "archive", args.rev],
                                 cwd=root, capture_output=True)
        if archive.returncode != 0:
            sys.stderr.write(archive.stderr.decode("utf-8", "replace"))
            return archive.returncode
        # tar rather than shelling a pipe, so this works the same on Windows
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tf:
            tf.extractall(tmp)

        tracked = sum(len(files) for _r, _d, files in os.walk(os.path.join(tmp, "data"))) \
            if os.path.isdir(os.path.join(tmp, "data")) else 0
        here = sum(len(files) for _r, _d, files in os.walk(os.path.join(root, "data"))) \
            if os.path.isdir(os.path.join(root, "data")) else 0
        print(f"  data/ files    here {here}, in a clone {tracked}")
        print(f"  extracted      {tmp}")
        print(f"  running        pytest {' '.join(args.pytest_args)}\n")

        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs",
                            *args.pytest_args], cwd=tmp)
        return r.returncode
    finally:
        if args.keep:
            print(f"\n  kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
