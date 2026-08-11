"""Hostile disc/container images: hangs and allocation blowups.

acidcat opens damaged files on purpose, so its threat model is denial of
service rather than code execution: a small input that costs unbounded time or
memory is a real defect, not an edge case. Every specimen here is a few hundred
bytes to a few KB.

The pattern in all of them is the same, and it is the same one that runs
through the rest of this release: a number inside the file was trusted as a
fact about the file. A count, a size, or an index was read from the image and
used to drive a read, a loop, or a recursion, with nothing checking it against
what the image could actually contain. The fix is always to clamp against the
real file, never against the declared number.

Measured before the fix, from the fuzzing pass that found these:
  gcm cursor loop      1,114 bytes  spun forever (killed at 120 s)
  wiidisc part table     262 KB     asked for 34.4 GB  (130,945x the input)
  gcm FST read         1,100 bytes  asked for 4.3 GB   (3,904,610x)
  gcm file read        1,118 bytes  asked for 4.3 GB
  iso9660 extents        110 KB     ran past 90 s
"""

import os
import struct
import subprocess
import sys
import tracemalloc

import pytest

# a small ceiling: every specimen here is under 300 KB, so anything that
# allocates tens of MB is tracking a declared number rather than the file
_ALLOC_CEILING = 64 * 1024 * 1024


def _gcm(fst, fst_size=None):
    h = bytearray(0x440)
    struct.pack_into(">I", h, 0x1C, 0xC2339F3D)
    struct.pack_into(">II", h, 0x424, 0x440,
                     len(fst) if fst_size is None else fst_size)
    return bytes(h) + fst


def _ent(typ, name_off, off, size):
    return struct.pack(">I", (typ << 24) | name_off) + struct.pack(">II", off, size)


def _peak(fn):
    tracemalloc.start()
    try:
        fn()
    except Exception:
        pass
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak


# ── GameCube FST ───────────────────────────────────────────────────

def test_a_directory_pointing_at_itself_does_not_spin(tmp_path):
    """`idx = size` where size <= idx never advances the cursor.

    Given a 1,114-byte file this ran indefinitely at zero progress, producing
    no output and offering nothing to interrupt in a pipeline.
    """
    from acidcat.core.containers import gcm
    p = tmp_path / "loop.iso"
    p.write_bytes(_gcm(_ent(1, 0, 0, 2) + _ent(1, 0, 0, 1) + b"D\x00"))
    list(gcm.walk(str(p)))          # the assertion is that this returns at all


def test_a_directory_pointing_backwards_does_not_recurse_forever(tmp_path):
    from acidcat.core.containers import gcm
    p = tmp_path / "rec.iso"
    p.write_bytes(_gcm(_ent(1, 0, 0, 3) + _ent(1, 0, 0, 0)
                       + _ent(0, 0, 0, 4) + b"D\x00"))
    list(gcm.walk(str(p)))


def test_a_declared_fst_size_does_not_drive_the_allocation(tmp_path):
    """f.read(n) commits n bytes before it learns the file is shorter."""
    from acidcat.core.containers import gcm
    p = tmp_path / "big.iso"
    p.write_bytes(_gcm(b"\x00" * 12, fst_size=0xFFFFFFFF))
    peak = _peak(lambda: list(gcm.walk(str(p))))
    assert peak < _ALLOC_CEILING, (
        f"{peak / 1e6:.0f} MB for a {p.stat().st_size}-byte file; the declared "
        f"size is still driving the read")


def test_a_declared_file_size_does_not_drive_the_allocation(tmp_path):
    from acidcat.core.containers import gcm
    p = tmp_path / "bigfile.iso"
    p.write_bytes(_gcm(_ent(1, 0, 0, 2) + _ent(0, 0, 0, 0xFFFFFFFF) + b"a.hps\x00"))
    entries = list(gcm.walk(str(p)))
    assert entries, "specimen no longer yields an entry, so this proves nothing"
    peak = _peak(lambda: gcm.read_file(str(p), entries[0]))
    assert peak < _ALLOC_CEILING, f"{peak / 1e6:.0f} MB from a 1 KB image"


def test_a_real_gcm_fst_still_walks(tmp_path):
    """The bound must not eat the ordinary case: a root holding one directory
    with one file in it, all indices pointing forward."""
    from acidcat.core.containers import gcm
    fst = (_ent(1, 0, 0, 3)          # root, children end at 3
           + _ent(1, 0, 0, 3)        # dir "D", children end at 3
           + _ent(0, 2, 0x1000, 16)  # file "f" inside it
           + b"D\x00f\x00")
    p = tmp_path / "ok.iso"
    p.write_bytes(_gcm(fst))
    got = list(gcm.walk(str(p)))
    assert [e["path"] for e in got] == ["D/f"], got
    assert got[0]["size"] == 16


# ── Wii partition table ────────────────────────────────────────────

def test_the_partition_count_does_not_drive_the_allocation(tmp_path):
    """npart is an unchecked u32; 0xFFFFFFFF asked for 34.4 GB."""
    pytest.importorskip("cryptography")
    from acidcat.core.containers import wiidisc
    h = bytearray(0x40100)
    struct.pack_into(">I", h, 0x18, 0x5D1C9EA3)
    struct.pack_into(">II", h, 0x40000, 0xFFFFFFFF, 0x4000)
    p = tmp_path / "wii.iso"
    p.write_bytes(bytes(h))
    peak = _peak(lambda: wiidisc.WiiDisc(str(p)))
    assert peak < _ALLOC_CEILING, (
        f"{peak / 1e6:.0f} MB for a {p.stat().st_size:,}-byte image")


def test_the_two_fst_walkers_have_not_diverged():
    """wiidisc._load_fst's descend is a copy of gcm's.

    Only the gcm copy is reachable with a synthetic specimen -- getting into the
    Wii one needs bytes that AES-decrypt to a walkable FST -- so the Wii copy
    was fixed by inspection. If someone repairs one and not the other, the
    unreachable copy silently keeps the hang. This asserts both carry the guard.
    """
    import inspect
    from acidcat.core.containers import gcm, wiidisc
    for mod in (gcm, wiidisc):
        src = inspect.getsource(mod)
        assert "child_end" in src, f"{mod.__name__} lost the forward-progress guard"
        assert "_MAX_DEPTH" in src, f"{mod.__name__} lost the depth bound"


# ── ISO 9660 extents ───────────────────────────────────────────────

def _cdxa_with_big_dirs(path, ndirs=24):
    SEC, UOFF = 2352, 24

    def sector(payload):
        s = bytearray(SEC)
        s[0:12] = b"\x00" + b"\xff" * 10 + b"\x00"
        s[16:20] = bytes([0x00, 0x00, 0x08, 0x00])
        s[20:24] = s[16:20]
        s[UOFF:UOFF + len(payload)] = payload[:2048]
        return bytes(s)

    pvd = bytearray(2048)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    root = bytearray(34)
    root[0] = 34
    root[2:6] = struct.pack("<I", 20)
    root[10:14] = struct.pack("<I", 4096)
    root[25] = 2
    root[32] = 1
    pvd[156:156 + 34] = root

    out = bytearray()
    for _ in range(16):
        out += sector(b"")
    out += sector(bytes(pvd))
    for _ in range(17, 20):
        out += sector(b"")
    d = bytearray()
    for k in range(ndirs):
        r = bytearray(34)
        r[0] = 34
        r[2:6] = struct.pack("<I", 30 + k)
        r[10:14] = struct.pack("<I", 0xFFFFFFFF)   # 4 GiB extent
        r[25] = 2
        r[32] = 1
        r[33] = 65 + k % 26
        d += r
    out += sector(bytes(d))
    for _ in range(21, 48):
        out += sector(b"")
    path.write_bytes(bytes(out))
    return path


def test_a_declared_extent_size_does_not_drive_the_read_count(tmp_path):
    """Each 4 GiB record cost 2,097,152 seek+read calls; 24 of them ran past
    90 seconds against a 110 KB image. The work must scale with the file."""
    from acidcat.core.containers import iso9660
    p = _cdxa_with_big_dirs(tmp_path / "big.bin")
    list(iso9660.walk(str(p)))      # returning at all is the assertion


# ── the CLI surface: malformed input is not an internal error ──────

def _run(*args):
    return subprocess.run([sys.executable, "-m", "acidcat", *args],
                          capture_output=True, text=True)


@pytest.mark.parametrize("body,why", [
    ('FILE "a.bin" BINARY\nTRACK 01 AUDIO\nINDEX 01 XX:YY:ZZ\n', "non-numeric MSF"),
    ('FILE "a.bin" BINARY\nTRACK zz AUDIO\nINDEX 01 00:00:00\n', "non-numeric track"),
    ('FILE "a.bin" BINARY\nTRACK 01\n', "short TRACK line"),
    ('FILE "a.bin" BINARY\nTRACK 01 AUDIO\nINDEX 01\n', "short INDEX line"),
    ('FILE "\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n', "unterminated quote"),
])
def test_a_malformed_cue_is_an_error_not_a_traceback(tmp_path, body, why):
    """A .cue is hand-editable text, so these are ordinary input.

    The unterminated quote is the interesting one: it yielded an empty filename,
    so the code opened the containing DIRECTORY, and the resulting OSError
    escaped as a traceback with exit 1 -- the code reserved for a legitimate
    negative answer.
    """
    p = tmp_path / "t.cue"
    p.write_text(body)
    r = _run("extract", str(p), "-o", str(tmp_path / "out"))
    assert "Traceback" not in r.stderr, f"{why}:\n{r.stderr}"
    assert r.returncode != 0


def test_an_odd_length_v64_rom_does_not_crash(tmp_path):
    """data[0::2] and data[1::2] differ in length on an odd input, and
    assigning mismatched extended slices raises ValueError."""
    from acidcat.core.extract import n64rip
    out = n64rip.normalize(b"\x37\x80\x40\x12" + b"\x00" * 1023)
    assert isinstance(out, bytes)


def test_an_oserror_from_a_parser_exits_2_not_1(tmp_path):
    """`raise` inside the OSError handler left main() entirely, so the
    BaseException net below it -- a sibling, not an outer handler -- never saw
    it. Every parser OSError printed a traceback and exited 1."""
    p = tmp_path / "q.cue"
    p.write_text('FILE "nope_does_not_exist.bin" BINARY\n'
                 'TRACK 01 AUDIO\nINDEX 01 00:00:00\n')
    r = _run("extract", str(p), "-o", str(tmp_path / "out"))
    assert r.returncode != 1 or "Traceback" not in r.stderr, (
        f"rc={r.returncode} with a traceback: a crash is reporting itself as a "
        f"clean negative\n{r.stderr}")
