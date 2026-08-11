# Running the tests

    pytest                       # everything a fresh clone can run
    pytest -q tests/test_wav.py  # one file

## The corpus

Three parity sweeps in `test_grammar_wav.py` compare the grammar interpreter
against the hand-written walker across many real files. They need a corpus, and
which one you get depends on `ACIDCAT_CORPUS`:

| `ACIDCAT_CORPUS` | corpus used | sweep size |
|---|---|---|
| unset (default) | `tests/corpus_generated/`, built on demand by `tests/make_corpus.py` | 23 specimens |
| set to a directory | every `*.wav` under it, recursively | as large as you like |

    # the wide sweep, on a machine that has a sample library
    ACIDCAT_CORPUS=~/sample_packs pytest tests/test_grammar_wav.py
    ACIDCAT_CORPUS_LIMIT=200 ACIDCAT_CORPUS=~/sample_packs pytest   # cap it

### Why generated rather than committed

The corpus used to default to `~/sample_packs`, which exists on one developer's
machine. That made those three assertions expand to **6,998 of 8,515 collected
tests locally and three skips on CI** -- 82% of the headline test count was
unreproducible, and a green CI run was covering a sixth of what the number
implied.

Committing the real corpus was not an option: it is licensed third-party sample
content and this repository is public. So `make_corpus.py` generates one
instead. Every byte is synthetic and deterministic, and it deliberately targets
the places an interpreter and a walker diverge -- the `fmt`/`inst`/`acid`
regions the grammar describes, undescribed chunks (`smpl`, `cue `, `LIST`,
`fact`) that skeleton parity still compares, odd-sized payloads that need a pad
byte, `WAVE_FORMAT_EXTENSIBLE` where the real tag hides in a GUID, `data`
before `fmt`, an empty `data`, and bytes past the declared RIFF end.

Regenerate at any time; it is idempotent:

    python tests/make_corpus.py

The generator is committed, its output is not (`.gitignore`).

## Skips

`addopts = "-rs"` in `pyproject.toml` means every skip prints its reason. If a
run says something was skipped, the log says why. Skips that remain on CI are
platform-conditional (the Linux-only sandbox profiles) or need a local corpus
of a format we cannot synthesise yet (`nksf`, `nmsv`, `krz`).

## Before you push

    python scripts/preflight.py

CI has nothing git does not carry. This machine has a 2,327-file sample
library, a 16 MB format-fixture tree and 731 MB of instrument packs -- all
gitignored, all invisible to a test that forgets to guard for them. Preflight
hides them and clears `ACIDCAT_*`, so a green run locally means a green run
there.

Every red build this project has had came from that gap rather than a real
defect: a test hardcoding `/tmp/...`, a test relying on the local registry, and
three TUI `copyfile` calls that had never been allowed to run. All three passed
here and failed on a runner.

    python scripts/preflight.py --full     # the wide run, local corpora included
