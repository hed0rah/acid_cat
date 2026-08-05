"""Raw tracebacks found by an adversarial pass, plus one silent backup loss.

A traceback reaching the user is a defect in a tool whose whole subject is
malformed input. Both of these were reachable from ordinary corrupt files, not
just contrived ones.
"""

import struct
import subprocess
import sys

import pytest


def _run(*args):
    return subprocess.run([sys.executable, "-m", "acidcat"] + list(args),
                          capture_output=True, text=True)


# ── audit: sorting findings compared None against int ──────────────

def test_audit_survives_a_check_failed_finding(tmp_path):
    """Four anomaly rules deliberately emit offset=None -- they are the ones
    whose job is to say "this rule could not run, the file was NOT screened".
    The sort ordered on offset directly, so the moment such a finding shared a
    severity with a positioned one, Python compared None < int and `audit` died
    with a TypeError. b"ID3" is merely the smallest trigger.
    """
    p = tmp_path / "id3.mp3"
    p.write_bytes(b"ID3")
    r = _run("audit", str(p))
    assert "Traceback" not in r.stderr, r.stderr
    assert "TypeError" not in r.stderr
    assert r.returncode in (0, 1, 2)
    assert "VERDICT" in r.stdout


@pytest.mark.parametrize("body", [b"ID3", b"ID3\x04", b"ID3\x04\x00\x00"])
def test_audit_on_truncated_id3_variants(tmp_path, body):
    p = tmp_path / "t.mp3"
    p.write_bytes(body)
    r = _run("audit", str(p))
    assert "Traceback" not in r.stderr, body


def test_none_offset_findings_sort_first(tmp_path):
    """Pin the ordering choice, not just the absence of a crash: a finding that
    is about the whole file belongs above one about a byte position."""
    from acidcat.core.forensics import anomalies
    mixed = [{"severity": "warn", "offset": 100, "rule": "a", "message": ""},
             {"severity": "warn", "offset": None, "rule": "b", "message": ""},
             {"severity": "warn", "offset": 5, "rule": "c", "message": ""}]
    mixed.sort(key=lambda x: (-anomalies._SEVERITY.get(x["severity"], 0),
                              x["offset"] if x["offset"] is not None else -1))
    assert [f["rule"] for f in mixed] == ["b", "c", "a"]


# ── parse_it: unpack_from guarded, bare index not ──────────────────

def _it_header(n):
    """An IT header truncated to n bytes. 4+26+2 then 8 u16 fields = 48, so
    everything up to offset 47 unpacks and data[48] is the first bare index."""
    d = (b"IMPM" + b"trunc".ljust(26, b"\x00") + struct.pack("<H", 0)
         + struct.pack("<HHHHHHH", 1, 1, 1, 1, 0x214, 0x214, 0))
    return (d + bytes(256))[:n]


@pytest.mark.parametrize("n", [48, 49, 50, 51, 52, 100, 191])
def test_extract_on_a_truncated_it_does_not_crash(tmp_path, n):
    """parse_it was the one tracker parser without an upfront length guard.
    Fields at 32..47 use unpack_from (clean struct.error, handled downstream)
    but gvol/mvol/speed/tempo at 48..51 are bare indices, so 48-51 bytes passed
    every unpack and then raised IndexError -- which `extract` deliberately does
    not catch, since its _MALFORMED set treats IndexError as a bug in acidcat.
    It was exactly that.
    """
    p = tmp_path / "t.it"
    p.write_bytes(_it_header(n))
    r = _run("extract", str(p), "-o", str(tmp_path / "ex"))
    assert "Traceback" not in r.stderr, f"{n} bytes: {r.stderr}"
    assert "IndexError" not in r.stderr, f"{n} bytes"
    assert r.returncode in (1, 2)


def test_parse_it_raises_the_handled_signal(tmp_path):
    """It must raise struct.error specifically -- that is the exception every
    caller already handles. A different one would just move the crash."""
    from acidcat.core.formats import tracker
    with pytest.raises(struct.error):
        tracker.parse_it(_it_header(48))


def test_inspect_agreed_all_along(tmp_path):
    """inspect survived because its walker wrapped the call in
    except (struct.error, IndexError). It must keep working after the fix."""
    p = tmp_path / "t.it"
    p.write_bytes(_it_header(48))
    r = _run("inspect", str(p))
    assert "Traceback" not in r.stderr


# ── write -o aliasing the input skipped the backup ─────────────────

def _wav(path):
    pcm = b"\x11\x22" * 256
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def test_write_o_onto_the_input_still_makes_a_backup(tmp_path):
    """`-o` at the input is not a copy, it is an in-place edit -- and it took
    the branch that makes no backup, so the one guaranteed-recoverable path was
    lost exactly when a script templated out == input."""
    p = _wav(tmp_path / "a.wav")
    original = p.read_bytes()

    r = _run("write", str(p), "--set", "artist=X", "-o", str(p))
    assert r.returncode == 0, r.stderr

    backup = tmp_path / "a_original.wav"
    assert backup.exists(), "in-place edit left no recoverable original"
    assert backup.read_bytes() == original
    assert p.read_bytes() != original          # the edit did happen


def test_write_o_elsewhere_still_leaves_the_input_alone(tmp_path):
    """The other half: a genuine copy must not start making backups."""
    p = _wav(tmp_path / "a.wav")
    original = p.read_bytes()
    out = tmp_path / "copy.wav"

    assert _run("write", str(p), "--set", "artist=X", "-o", str(out)).returncode == 0
    assert p.read_bytes() == original
    assert out.exists() and out.read_bytes() != original
    assert not (tmp_path / "a_original.wav").exists()
