"""od must never refuse to show bytes, and must survive a closed pipe.

`od(1)` dumps anything you point it at; requiring a recognized format made
acidcat's od useless for the exact case a reverse engineer reaches for it --
a proprietary container with no walker. These pin the raw fallback, the range
selection that lets a located region be dumped, and the closed-pipe behaviour
that makes `acidcat od big.bin | head` safe.
"""

import io
import os
import struct
import subprocess
import sys

import pytest

from acidcat.commands import od as odcmd


class _Args:
    def __init__(self, target, **kw):
        self.target = target
        d = {"color": "never", "width": 16, "offset": None, "at": None,
             "length": None, "end": None, "region": None}
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


def _unknown(tmp_path, body=b"\xde\xad\xbe\xef" * 64):
    """A file no walker recognizes -- the case that used to be refused."""
    p = tmp_path / "proprietary.ch1"
    p.write_bytes(body)
    return str(p)


def test_unknown_format_is_dumped_not_refused(tmp_path, capsys):
    path = _unknown(tmp_path)
    rc = odcmd.run(_Args(path))
    out = capsys.readouterr().out
    assert rc == 0, "od refused a file it could not walk"
    assert "raw dump" in out
    assert "de ad be ef" in out, "the bytes themselves were not shown"


def test_walkable_file_still_gets_the_annotated_layout(tmp_path, capsys):
    """The fallback must not cost the structural dump for real formats."""
    wav = os.path.join("data", "test_formats", "generated", "src.wav")
    if not os.path.isfile(wav):
        pytest.skip("test corpus WAV not present")
    rc = odcmd.run(_Args(wav))
    out = capsys.readouterr().out
    assert rc == 0
    assert "fmt " in out and "sample_rate" in out, "lost the field annotation"
    assert "raw dump" not in out


def test_explicit_range_dumps_exactly_that(tmp_path, capsys):
    body = bytes(range(256))
    path = _unknown(tmp_path, body)
    rc = odcmd.run(_Args(path, offset="0x10", length="16"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "10 11 12 13" in out, "wrong bytes for the requested range"
    # exactly one dump row (the header line carries the range, so count rows)
    rows = [l for l in out.splitlines() if l.startswith("  0x")]
    assert len(rows) == 1, f"expected 1 row of 16 bytes, got {len(rows)}"
    assert "20 21 22" not in out, "dumped past the requested length"


def test_end_offset_form(tmp_path, capsys):
    path = _unknown(tmp_path, bytes(range(256)))
    odcmd.run(_Args(path, offset="0", end="0x08"))
    out = capsys.readouterr().out
    assert "00 01 02 03 04 05 06 07" in out
    assert "0x00000010" not in out


def test_anchored_start(tmp_path, capsys):
    """--at find: lets you dump from a pattern without hand-counting."""
    path = _unknown(tmp_path, b"\x00" * 32 + b"MARK" + b"\x11" * 16)
    rc = odcmd.run(_Args(path, at="find:MARK", length="8"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "4d 41 52 4b" in out, "did not start at the anchor"


def test_region_selects_a_located_blob(tmp_path, capsys):
    """--region N dumps what `locate` found, so an RE workflow does not need a
    manual offset copy-paste."""
    # a blob with a real WAV embedded partway in, so locate has something to find
    wav = os.path.join("data", "test_formats", "generated", "src.wav")
    if not os.path.isfile(wav):
        pytest.skip("test corpus WAV not present")
    payload = open(wav, "rb").read()
    p = tmp_path / "blob.img"
    p.write_bytes(b"\x00" * 2048 + payload)

    rc = odcmd.run(_Args(str(p), region=0))
    out = capsys.readouterr().out
    assert rc == 0
    assert "0x00000800" in out, "region 0 should start at the embedded WAV"
    assert "52 49 46 46" in out, "expected the RIFF magic at the region start"


def test_region_out_of_range_is_a_usage_error(tmp_path, capsys):
    wav = os.path.join("data", "test_formats", "generated", "src.wav")
    if not os.path.isfile(wav):
        pytest.skip("test corpus WAV not present")
    p = tmp_path / "blob.img"
    p.write_bytes(b"\x00" * 2048 + open(wav, "rb").read())
    rc = odcmd.run(_Args(str(p), region=99))
    err = capsys.readouterr().err
    assert rc == 2
    assert "out of range" in err


def test_closed_pipe_exits_cleanly():
    """`acidcat od big | head` must not traceback. Windows raises OSError
    EINVAL here rather than BrokenPipeError, which the handler has to know."""
    src = os.path.join("data", "test_formats", "generated", "src.wav")
    if not os.path.isfile(src):
        pytest.skip("test corpus WAV not present")
    env = dict(os.environ, PYTHONPATH=os.path.join(os.getcwd(), "src"))
    # emulate `| head -1` portably: read one line, then close the read end so
    # the producer's next write hits a pipe with no reader. Doing this with
    # Popen rather than a shell keeps the test honest on Windows, where
    # `shell=True` is cmd.exe and would not reproduce the failure at all.
    producer = subprocess.Popen(
        [sys.executable, "-m", "acidcat", "od", src, "--offset", "0",
         "--length", "400000"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        producer.stdout.readline()
        producer.stdout.close()
        producer.wait(timeout=60)
        err = producer.stderr.read()
    finally:
        producer.stderr.close()
        if producer.poll() is None:
            producer.kill()
    text = err.decode(errors="replace")
    assert "Traceback" not in text, text[:400]
    assert "Invalid argument" not in text, "the Windows EINVAL path leaked"
    assert producer.returncode == 0, f"exit {producer.returncode}: {text[:200]}"
