"""A registered --output-format must actually change the output.

`scan` and `features` both call add_output_format_arg() and then, on the
directory path, ignored the result entirely -- always writing a CSV file. So
`scan DIR --json` accepted the flag, produced CSV, and piped nothing at all.
argparse accepting a flag is not the same as the command honouring it, and
nothing in the suite could tell the two apart.
"""

import json
import struct
import subprocess
import sys


def _wav(path, rate=44100, secs=0.05):
    n = int(rate * secs)
    data = b"\x00\x00" * n
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _run(args, cwd):
    return subprocess.run([sys.executable, "-m", "acidcat"] + args,
                          cwd=str(cwd), capture_output=True, text=True)


def test_scan_json_goes_to_stdout(tmp_path):
    src = tmp_path / "lib"
    src.mkdir()
    _wav(src / "a.wav")
    _wav(src / "b.wav")

    r = _run(["scan", str(src), "--json", "-q"], tmp_path)
    assert r.returncode == 0, r.stderr

    rows = json.loads(r.stdout)          # the whole point: parseable on a pipe
    assert len(rows) == 2
    assert {row["format"] for row in rows} == {"wav"}

    # and it must NOT have silently written the CSV file instead
    assert not list(tmp_path.glob("*.csv"))


def test_scan_default_still_writes_csv(tmp_path):
    """The default is load-bearing -- scripts depend on the CSV file."""
    src = tmp_path / "lib"
    src.mkdir()
    _wav(src / "a.wav")

    r = _run(["scan", str(src), "-q"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""
    csvs = list(tmp_path.glob("*.csv"))
    assert len(csvs) == 1
    assert csvs[0].read_text().startswith("filename,format,bpm,key")


def test_scan_json_to_output_file(tmp_path):
    src = tmp_path / "lib"
    src.mkdir()
    _wav(src / "a.wav")

    dest = tmp_path / "out.json"
    r = _run(["scan", str(src), "--json", "-o", str(dest), "-q"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(dest.read_text())[0]["format"] == "wav"
