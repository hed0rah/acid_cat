"""No verb may destroy the file it is reading.

`carve -o` pointed at its own input truncated a 2,044-byte WAV to the 4 bytes it
had been asked to extract, and exited 0. That was found and fixed; a source
audit then found `convert` has the same shape at four write sites, and `wrap` at
one. convert is the worse of the two, because it can reach the bug WITHOUT `-o`:
the default output name is the input's stem plus the target extension, so a
`.wav` converted to WAV lands on top of itself.

Neither is a torn write -- both read the input fully into memory first -- so the
bytes that land are correct. The file that was there before is simply gone, and
both commands' help says the input is never modified.

`repair` and `write` are safe by a different route (atomic temp + os.replace on
fully-computed data) and are asserted here so the guarantee is stated in one
place.
"""

import hashlib
import struct
import subprocess
import sys

import pytest


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _pcm_wav(path):
    pcm = b"\x11\x22" * 512
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def _adpcm_wav(path):
    """IMA ADPCM, so `convert --to-pcm` actually has work to do and reaches the
    write. A plain PCM file exits early on 'already PCM' and proves nothing."""
    nblocks, ba = 8, 256
    blocks = b"".join(struct.pack("<hBB", 1000, 0, 0) + bytes(ba - 4)
                      for _ in range(nblocks))
    fmt = (struct.pack("<HHIIHHH", 0x11, 1, 22050, 11025, ba, 4, 2)
           + struct.pack("<H", (ba - 4) * 2 + 1))
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"fact" + struct.pack("<II", 4, nblocks * ((ba - 4) * 2 + 1))
            + b"data" + struct.pack("<I", len(blocks)) + blocks)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def _run(*args):
    return subprocess.run([sys.executable, "-m", "acidcat"] + list(args),
                          capture_output=True, text=True)


def test_carve_refuses_its_own_input(tmp_path):
    p = _pcm_wav(tmp_path / "v.wav")
    before = _sha(p)
    r = _run("carve", str(p), "--chunk", "data", "-o", str(p))
    assert r.returncode == 2
    assert _sha(p) == before


def test_convert_refuses_its_own_input(tmp_path):
    p = _adpcm_wav(tmp_path / "a.wav")
    before = _sha(p)
    r = _run("convert", str(p), "--to-pcm", "-o", str(p))
    assert r.returncode == 2, r.stderr
    assert "output is the input" in r.stderr
    assert _sha(p) == before


def test_wrap_refuses_its_own_input(tmp_path):
    p = tmp_path / "raw.pcm"
    p.write_bytes(b"\x01\x02" * 512)
    before = _sha(p)
    r = _run("wrap", str(p), "-o", str(p))
    assert r.returncode == 2
    assert _sha(p) == before


def test_convert_to_a_different_name_still_works(tmp_path):
    """The guard must not cost the capability."""
    p = _adpcm_wav(tmp_path / "a.wav")
    out = tmp_path / "out.wav"
    r = _run("convert", str(p), "--to-pcm", "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert out.read_bytes()[:4] == b"RIFF"
    assert out.stat().st_size > p.stat().st_size      # decoded, so bigger


def test_carve_to_a_different_name_still_works(tmp_path):
    p = _pcm_wav(tmp_path / "v.wav")
    out = tmp_path / "data.raw"
    assert _run("carve", str(p), "--chunk", "data", "-o", str(out)).returncode == 0
    assert out.stat().st_size == 1024


@pytest.mark.parametrize("verb,extra", [
    ("repair", []),
    ("write", ["--set", "title=x"]),
])
def test_repair_and_write_are_safe_in_place(tmp_path, verb, extra):
    """These two DO write in place, by design, and are safe because they go
    through an atomic temp + replace on fully-computed data. Pinned so the
    distinction stays deliberate rather than accidental."""
    p = _pcm_wav(tmp_path / "v.wav")
    r = _run(verb, str(p), *extra)
    assert r.returncode in (0, 1, 2), r.stderr
    data = p.read_bytes()
    assert data[:4] == b"RIFF" and b"data" in data      # still a readable WAV
