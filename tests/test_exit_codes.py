"""Exit codes are the scripting contract, so they get pinned.

The convention, following grep and diff:

    0   it worked: the file is clean, the thing you asked for is here
    1   it ran fine and the answer is no: nothing matched, nothing found,
        or the file has something to answer for
    2   it could not run: bad flag, bad value, missing or unreadable input,
        or nothing in the input was checkable

Before 1.0 this was a habit rather than a rule, and the gaps were not cosmetic:

  - `locate` exited 0 having found nothing, so `locate --json | carve --batch -`
    on a blob with no audio in it succeeded all the way through and a recovery
    script carried on with an empty output directory.
  - `validate` exited 0 for files it never modelled, giving a clean bill of
    health to anything it did not understand -- on the same byte where `audit`
    had findings.
  - `audit` always exited 0, so the forensic verb could not gate anything.
  - `repair --dry-run` exited 0 over a list of pending repairs.
  - a missing file was 1 in eleven verbs and 2 in three.
  - `carve --chunk ZZZZ` was 2 (you typed it wrong) where `dump FILE ZZZZ` was
    1 (it is not in this file) for the identical question.

The previous version of the missing-file test asserted `in (1, 2)` across three
verbs, which encoded the disagreement rather than catching it. It is now
parametrized over every verb that takes a path.
"""

import struct

import pytest

from acidcat.cli import main


def _wav(n_frames=32):
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", n_frames * 2) + bytes(n_frames * 2))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


@pytest.fixture
def empty_registry(tmp_path, monkeypatch):
    """Point the registry somewhere empty.

    These assertions used to pass on a developer machine and fail on CI for a
    reason that had nothing to do with what they were testing: with libraries
    registered, query reached the range parser and returned 2; with none, it
    returned "no libraries" (1) first. Validating the invocation before any
    state is the fix, and running against an empty registry is what proves it.
    """
    monkeypatch.setenv("ACIDCAT_REGISTRY", str(tmp_path / "reg.sqlite"))
    return str(tmp_path / "reg.sqlite")


def test_a_bad_filter_value_is_a_usage_error(capsys, empty_registry):
    assert main(["query", "--registry", empty_registry, "--bpm", "zzz"]) == 2
    assert "bad --bpm value" in capsys.readouterr().err


def test_a_bad_range_does_not_traceback(capsys, empty_registry):
    """The bare branch was guarded and the range branch was not, so
    `--bpm a:b` raised an uncaught ValueError."""
    assert main(["query", "--registry", empty_registry, "--bpm", "a:b"]) == 2
    assert "bad --bpm range" in capsys.readouterr().err


def test_a_valid_filter_against_an_empty_registry_is_not_a_usage_error(
        capsys, empty_registry):
    """The other side of the same fix: a well-formed query with nothing to
    search is 1 (ran, no answer), not 2 (you typed it wrong)."""
    assert main(["query", "--registry", empty_registry, "--bpm", "120"]) == 1


def test_shape_on_a_missing_path_is_not_silent_success(tmp_path, capsys):
    assert main(["shape", str(tmp_path / "nope")]) == 2
    assert "No such file" in capsys.readouterr().err


def test_shape_on_a_real_file_succeeds(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    assert main(["shape", str(p)]) == 0


@pytest.mark.parametrize("argv", [
    ["probe", "--help"],
    ["carve", "--help"],
    ["wrap", "--help"],
])
def test_help_is_success(argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 0


def _code(argv):
    """Exit code for argv, however it is delivered. argparse raises
    SystemExit(2) for its own usage errors; a command returns an int."""
    try:
        return main(argv)
    except SystemExit as e:
        return e.code


# every verb that accepts a range expression -- checked as a set rather than a
# hand-picked pair, because the first version of this test asserted only `od`
# and `probe` and therefore missed `inspect`, which returned 1 for the same
# mistake right up until a regression audit found it
_RANGE_VERBS = [
    ("od", lambda p: ["od", p, "--at", "notanoffset"]),
    ("carve", lambda p: ["carve", p, "--at", "notanoffset", "--length", "4"]),
    ("inspect", lambda p: ["inspect", p, "--at", "notanoffset"]),
    ("probe", lambda p: ["probe", p, "read", "notanoffset"]),
]


@pytest.mark.parametrize("verb,argv", _RANGE_VERBS, ids=[v for v, _ in _RANGE_VERBS])
def test_the_same_mistake_gets_the_same_code_in_every_verb(tmp_path, verb, argv):
    """An unresolvable offset expression is a usage error everywhere, or a
    script cannot branch on it without knowing which verb it called."""
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    assert _code(argv(str(p))) == 2, f"{verb} disagrees on a bad range expression"


# Every verb that takes a file path. The old version of this test checked three
# verbs and asserted `in (1, 2)` -- it encoded the disagreement instead of
# catching it, and eleven verbs said 1 while three said 2 for the same typo.
_FILE_VERBS = [
    ("classify", lambda p: ["classify", p]),
    ("locate", lambda p: ["locate", p]),
    ("carve", lambda p: ["carve", p, "--trailing"]),
    ("inspect", lambda p: ["inspect", p]),
    ("dump", lambda p: ["dump", p, "fmt"]),
    ("extract", lambda p: ["extract", p]),
    ("repair", lambda p: ["repair", p]),
    ("audit", lambda p: ["audit", p]),
    ("info", lambda p: ["info", p]),
    ("probe", lambda p: ["probe", p, "strings"]),
    ("chunks", lambda p: ["chunks", p]),
    ("cover", lambda p: ["cover", p]),
    ("convert", lambda p: ["convert", p]),
    ("od", lambda p: ["od", p]),
    ("validate", lambda p: ["validate", p]),
    ("shape", lambda p: ["shape", p]),
]


@pytest.mark.parametrize("verb,argv", _FILE_VERBS, ids=[v for v, _ in _FILE_VERBS])
def test_a_missing_input_is_two_everywhere(tmp_path, verb, argv):
    """2 = could not run. A path that is not there is the same failure whichever
    verb you handed it to."""
    assert _code(argv(str(tmp_path / "nope.wav"))) == 2, verb


def test_a_clean_file_exits_zero(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    assert main(["validate", str(p)]) == 0
    assert main(["audit", str(p)]) == 0
    assert main(["classify", str(p)]) == 0
    assert main(["shape", str(p)]) == 0
    assert main(["repair", "--dry-run", str(p)]) == 0


def _stale_riff(tmp_path, name="broken.wav"):
    """A WAV whose RIFF size field disagrees with the file -- one violation,
    repairable, and every structural verb should agree it is not clean."""
    raw = bytearray(_wav())
    raw[4:8] = struct.pack("<I", 99999)
    p = tmp_path / name
    p.write_bytes(bytes(raw))
    return p


def test_a_negative_answer_is_one_everywhere(tmp_path):
    """1 = it ran fine and the answer is no. These all used to be 0, so a
    recovery or gating script could not branch on any of them."""
    clean = tmp_path / "a.wav"
    clean.write_bytes(_wav())
    broken = _stale_riff(tmp_path)
    noise = tmp_path / "noise.img"
    noise.write_bytes(bytes((i * 37 + 11) % 256 for i in range(80000)))

    # locate found nothing -- the one that let `locate | carve` claim success
    assert main(["locate", "--min-confidence", "0.99", str(noise)]) == 1
    # audit has something to report
    assert main(["audit", str(broken)]) == 1
    # repair --dry-run has pending work, same answer validate gives
    assert main(["repair", "--dry-run", str(broken)]) == 1
    assert main(["validate", str(broken)]) == 1
    # a named region that is not in the file
    assert main(["dump", str(clean), "ZZZZ"]) == 1
    assert main(["carve", str(clean), "--chunk", "ZZZZ"]) == 1
    # a filter that matched nothing
    assert main(["shape", "--format", "flac", str(clean)]) == 1


def test_nothing_checkable_is_not_success(tmp_path):
    """2, not 0. `validate` gave a clean bill of health to files it never
    modelled, which is the worst possible answer from a gating verb."""
    p = tmp_path / "opaque.bin"
    p.write_bytes(bytes(range(256)) * 8)
    assert main(["validate", str(p)]) == 2
    assert main(["audit", str(p)]) == 2
    assert main(["repair", str(p)]) == 2


def test_the_recovery_pipeline_can_report_failure(tmp_path):
    """The end-to-end case the whole convention exists for.

    A shell pipeline reports the LAST command's status, so fixing `locate`
    alone was not enough -- `carve --batch` is the code the script sees, and it
    returned 0 after carving nothing.
    """
    blob = tmp_path / "noise.img"
    blob.write_bytes(bytes((i * 37 + 11) % 256 for i in range(80000)))
    empty = tmp_path / "regions.json"
    empty.write_text("[]")
    out = tmp_path / "out"

    assert main(["carve", str(blob), "--batch", str(empty), "-o", str(out)]) == 1
    assert not list(out.iterdir())

    # and the success side still succeeds
    real = tmp_path / "a.wav"
    real.write_bytes(_wav())
    recs = tmp_path / "one.json"
    recs.write_text('[{"offset": 0, "length": 44, "kind": "container"}]')
    out2 = tmp_path / "out2"
    assert main(["carve", str(real), "--batch", str(recs), "-o", str(out2)]) == 0
    assert len(list(out2.iterdir())) == 1


def test_carve_separates_not_found_from_bad_usage(tmp_path):
    """Both used to be 2, so `carve --chunk X` could not be told apart from a
    typo in the invocation."""
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    assert main(["carve", str(p), "--chunk", "ZZZZ"]) == 1        # not there
    assert _code(["carve", str(p), "--chunk", "fmt", "--trailing"]) == 2  # typo
