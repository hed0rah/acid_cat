"""acidcat performance baseline -- one tracked number per verb.

Turns performance into a value that can regress visibly, instead of a feeling.
Drives every verb through the REAL argparse dispatcher (the exact path a user
hits) with stdout and stderr sent to devnull, so rendering cost is counted and
console I/O is not.

    python scripts/bench.py                      # generated corpus, reproducible anywhere
    python scripts/bench.py --corpus ~/samples   # real-world numbers
    python scripts/bench.py --json baseline.json # write a baseline to compare against
    python scripts/bench.py --compare baseline.json   # diff against a saved one

Design decisions worth knowing:

* **Deterministic input.** Files are chosen by a sorted walk and capped per verb,
  so the same corpus yields the same selection every run.
* **A corpus by default.** `--corpus` was originally required, which meant the
  baseline could only be reproduced by someone holding the same private sample
  library. Without it we generate the same synthetic specimens the test suite
  uses, so any clone can produce a comparable number. Real corpora give truer
  absolute figures; the generated one gives a figure two machines can compare.
* **Warm and cold both reported.** A warm-up pass primes the page cache and the
  lazy imports; the headline is the median of N timed passes, but the cold pass
  is printed because first-run latency is what a user actually feels.
* **Two metrics on purpose.** ms/file is honest for the walkers, which cap their
  reads so their MB/s looks inflated; MB/s is what survives a change of machine.
* **Variance, not best-of.** min/median/max are all emitted, so a noisy run
  looks noisy instead of looking fast.
* **Interpreter startup is measured separately.** It is a per-invocation
  constant, not a per-file rate, and folding it in would flatter the slow verbs.

NOTHING HERE MAY WRITE TO THE CORPUS. `repair` and `write` mutate files, so they
are benchmarked in --dry-run only, and _MUTATING guards that: the first version
of this harness benchmarked `repair` with no flags, which would have rewritten a
user's samples and littered `_original` backups the moment it met a file with a
stale size. It survived on the accident that the corpus was already consistent.
"""

import argparse
import contextlib
import gc
import json
import os
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from acidcat.cli import _build_parser                              # noqa: E402

# verb -> (extensions to feed it, per-verb file cap, extra argv)
PLAN = {
    "info":     ({".wav", ".aif", ".aiff", ".flac", ".mp3"}, 300, ()),
    "inspect":  ({".wav", ".aif", ".aiff", ".flac"}, 300, ()),
    "classify": ({".wav", ".aif", ".aiff", ".flac", ".mp3"}, 300, ()),
    "shape":    ({".wav", ".aif", ".aiff", ".flac"}, 300, ()),
    "chunks":   ({".wav"}, 300, ()),
    "od":       ({".wav", ".aif", ".aiff"}, 200, ()),
    "audit":    ({".wav", ".aif", ".aiff", ".flac"}, 200, ()),
    "validate": ({".wav", ".aif", ".aiff", ".flac"}, 300, ()),
    "repair":   ({".wav", ".aif", ".aiff"}, 300, ("--dry-run",)),
    "locate":   ({".wav"}, 60, ()),
}

# Verbs that can write to their input. Benchmarking one of these without a
# read-only flag would modify the corpus being measured, which is both a
# destroyed sample library and a meaningless second run.
_MUTATING = {"repair": "--dry-run", "write": None, "convert": None,
             "carve": None, "cover": None, "wrap": None, "extract": None}


def _check_readonly():
    """Refuse to run if a mutating verb is planned without its read-only flag."""
    for verb, (_exts, _cap, extra) in PLAN.items():
        need = _MUTATING.get(verb, "")
        if need == "":
            continue
        if need is None:
            raise SystemExit(f"bench: {verb} writes to its input and has no "
                             f"read-only mode; remove it from PLAN")
        if need not in extra:
            raise SystemExit(f"bench: {verb} writes to its input; PLAN must "
                             f"pass {need}")


def generated_corpus(into):
    """The synthetic specimens the test suite uses -- license-clean, tiny, and
    identical on every machine, so two people can compare baselines."""
    gen = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "tests", "make_corpus.py")
    os.makedirs(into, exist_ok=True)
    subprocess.run([sys.executable, gen, into], check=True,
                   stdout=subprocess.DEVNULL)
    return into


def select(corpus, exts, cap):
    out = []
    for dp, dn, fn in os.walk(corpus):
        dn.sort()
        for name in sorted(fn):
            if os.path.splitext(name)[1].lower() in exts:
                out.append(os.path.join(dp, name))
                if len(out) >= cap:
                    return out
    return out


@contextlib.contextmanager
def hush(devnull):
    so, se = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = devnull
    try:
        yield
    finally:
        sys.stdout, sys.stderr = so, se


def bench_verb(parser, verb, files, extra, repeats, devnull):
    def one_pass():
        for f in files:
            a, _ = parser.parse_known_args([verb, f, *extra])
            with hush(devnull):
                try:
                    a.func(a)
                except SystemExit:
                    pass
    nbytes = sum(os.path.getsize(f) for f in files)
    gc.collect()
    t = time.perf_counter()
    one_pass()
    cold = time.perf_counter() - t
    passes = []
    for _ in range(repeats):
        gc.collect()
        t = time.perf_counter()
        one_pass()
        passes.append(time.perf_counter() - t)
    med = statistics.median(passes)
    return {
        "verb": verb, "files": len(files), "bytes": nbytes,
        "cold_s": round(cold, 4),
        "warm_median_s": round(med, 4),
        "warm_min_s": round(min(passes), 4),
        "warm_max_s": round(max(passes), 4),
        "ms_per_file": round(med / len(files) * 1000, 3),
        "mb_per_s": round(nbytes / 1e6 / med, 1) if med else 0.0,
    }


def startup_ms():
    """Interpreter + import cost of one invocation -- the latency every user
    pays before any work happens."""
    ts = []
    for _ in range(3):
        t = time.perf_counter()
        subprocess.run([sys.executable, "-m", "acidcat", "--version"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ts.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(ts), 1)


def compare(rows, path):
    old = {r["verb"]: r for r in json.load(open(path))["rows"]}
    print(f"\n{'verb':10s} {'was':>10s} {'now':>10s} {'change':>10s}")
    for r in rows:
        o = old.get(r["verb"])
        if not o:
            print(f"{r['verb']:10s} {'--':>10s} {r['ms_per_file']:10.2f}  (new)")
            continue
        was, now = o["ms_per_file"], r["ms_per_file"]
        pct = (now - was) / was * 100 if was else 0.0
        mark = "" if abs(pct) < 10 else ("  SLOWER" if pct > 0 else "  faster")
        print(f"{r['verb']:10s} {was:10.2f} {now:10.2f} {pct:+9.1f}%{mark}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=None,
                    help="Directory of real files. Default: generate the "
                         "suite's synthetic corpus (reproducible anywhere).")
    ap.add_argument("--verbs", default=None, help="Comma list; default all.")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--json", default=None, help="Write the baseline here.")
    ap.add_argument("--compare", default=None, help="Diff against a saved baseline.")
    args = ap.parse_args()

    _check_readonly()

    # ACIDCAT_HOME, not just ACIDCAT_REGISTRY: the latter relocates registry.db
    # alone and leaves per-library index DBs landing in the real home.
    os.environ.setdefault("ACIDCAT_HOME", os.path.join(
        os.environ.get("TEMP", "."), "acidcat_bench_home"))

    corpus = args.corpus
    generated = corpus is None
    if generated:
        corpus = generated_corpus(os.path.join(
            os.environ["ACIDCAT_HOME"], "corpus"))

    parser = _build_parser()
    devnull = open(os.devnull, "w")
    want = args.verbs.split(",") if args.verbs else list(PLAN)

    print(f"corpus: {corpus}{'  (generated)' if generated else ''}")
    print(f"python: {sys.version.split()[0]}   startup: {startup_ms()} ms/invocation\n")
    print(f"{'verb':10s} {'n':>4s} {'MB':>7s} {'ms/file':>9s} {'MB/s':>9s} "
          f"{'cold':>7s}  warm min/med/max (s)")
    rows = []
    for verb in want:
        exts, cap, extra = PLAN[verb]
        files = select(corpus, exts, cap)
        if not files:
            print(f"{verb:10s}  (no matching files in this corpus)")
            continue
        r = bench_verb(parser, verb, files, extra, args.repeats, devnull)
        rows.append(r)
        print(f"{verb:10s} {r['files']:4d} {r['bytes']/1e6:7.2f} "
              f"{r['ms_per_file']:9.3f} {r['mb_per_s']:9.1f} {r['cold_s']:7.3f}  "
              f"{r['warm_min_s']:.3f}/{r['warm_median_s']:.3f}/{r['warm_max_s']:.3f}")

    if args.compare:
        compare(rows, args.compare)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"corpus": "generated" if generated else corpus,
                       "python": sys.version.split()[0],
                       "startup_ms": startup_ms(), "rows": rows}, f, indent=2)
        print(f"\nbaseline written to {args.json}")


if __name__ == "__main__":
    main()
