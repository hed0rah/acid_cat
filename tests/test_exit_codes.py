"""Exit codes are the scripting contract, so they get pinned.

The convention, following grep and argparse:

    0   it worked
    1   it ran fine and the answer is negative, or a runtime failure
    2   the invocation was wrong (bad flag, bad value, missing target)

Before 1.0 this was a habit rather than a rule: `probe`, `index` and `inspect`
returned 1 for usage errors while `od`, `write` and `repair` returned 2 for the
identical class; `query` raised a string SystemExit (which exits 1 and bypasses
dispatch entirely); and `shape /no/such/path` printed nothing and exited 0. A
script cannot branch on that.
"""

import struct

import pytest

from acidcat.cli import main


def _wav(n_frames=32):
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", n_frames * 2) + bytes(n_frames * 2))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_a_bad_filter_value_is_a_usage_error(capsys):
    assert main(["query", "--bpm", "zzz"]) == 2
    assert "bad --bpm value" in capsys.readouterr().err


def test_a_bad_range_does_not_traceback(capsys):
    """The bare branch was guarded and the range branch was not, so
    `--bpm a:b` raised an uncaught ValueError."""
    assert main(["query", "--bpm", "a:b"]) == 2
    assert "bad --bpm range" in capsys.readouterr().err


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


def test_the_same_mistake_gets_the_same_code_in_every_verb(tmp_path):
    """The inconsistency that motivated this: an unresolvable offset expression
    returned 2 from `od` and 1 from `probe`, so a script could not branch on
    it without knowing which verb it had called."""
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    assert _code(["od", str(p), "--at", "notanoffset"]) == 2
    assert _code(["probe", str(p), "read", "notanoffset"]) == 2


def test_a_missing_input_file_is_consistent(tmp_path):
    missing = str(tmp_path / "nope.wav")
    for argv in (["od", missing], ["locate", missing], ["chunks", missing]):
        assert _code(argv) in (1, 2), argv


def test_a_clean_file_exits_zero(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    assert main(["validate", str(p)]) == 0
    assert main(["audit", str(p)]) == 0
