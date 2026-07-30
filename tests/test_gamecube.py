"""Tests for GameCube support: DSP-ADPCM decode, the GCM disc walk, and HPS
streams. Synthetic data with all-zero coefficients makes the DSP predictor
memoryless, so a nibble decodes to exactly nibble << scale."""
import array
import struct

import pytest

from acidcat.core.containers import gcm
from acidcat.core.codecs import dsp, hps


def test_dsp_decode_exact():
    coefs = [0] * 16                                  # memoryless predictor
    frame = bytes([0x00, 0x21, 0, 0, 0, 0, 0, 0])     # header 0 (pred 0, scale 2^0=1)
    s = array.array("h")
    s.frombytes(dsp.decode(frame, coefs))
    assert len(s) == 14
    assert s[0] == 2 and s[1] == 1                    # high nibble then low nibble of 0x21
    assert s[2] == 0


def _gcm_image(name, body, fst_off=0x1000, data_off=0x2000):
    img = bytearray(data_off + len(body))
    struct.pack_into(">I", img, 0x1C, gcm.MAGIC)
    struct.pack_into(">II", img, 0x424, fst_off, 0x100)
    num = 2
    struct.pack_into(">I", img, fst_off, (1 << 24) | 0)          # root: type 1, name 0
    struct.pack_into(">II", img, fst_off + 4, 0, num)            # parent, entry count
    struct.pack_into(">I", img, fst_off + 12, (0 << 24) | 1)     # file: type 0, name @1
    struct.pack_into(">II", img, fst_off + 16, data_off, len(body))
    nm = b"\x00" + name.encode() + b"\x00"
    img[fst_off + num * 12:fst_off + num * 12 + len(nm)] = nm
    img[data_off:data_off + len(body)] = body
    return bytes(img)


def test_gcm_walk(tmp_path):
    p = tmp_path / "game.iso"
    p.write_bytes(_gcm_image("HELLO.HPS", b"AUDIO"))
    assert gcm.is_gcm(str(p))
    files = list(gcm.walk(str(p)))
    assert len(files) == 1
    assert files[0]["path"] == "HELLO.HPS" and files[0]["size"] == 5
    assert gcm.read_file(str(p), files[0]) == b"AUDIO"

    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"not a disc" + bytes(0x500))
    assert not gcm.is_gcm(str(plain))
    assert list(gcm.walk(str(plain))) == []


def test_hps_decode():
    data = bytearray(b" HALPST\x00" + struct.pack(">II", 22050, 1))  # rate, 1 channel
    data += bytes(0x38)                               # one channel context, coefs zero
    blk = bytearray(0x20)
    frame = bytes([0x00, 0x21, 0, 0, 0, 0, 0, 0])
    struct.pack_into(">I", blk, 0, len(frame))        # dsp_size (1 channel)
    struct.pack_into(">I", blk, 8, 0xFFFFFFFF)        # next_off: end
    data += blk + frame

    pcm, info = hps.decode(bytes(data))
    assert info["channels"] == 1 and info["rate"] == 22050
    s = array.array("h"); s.frombytes(pcm)
    assert s[0] == 2 and s[1] == 1

    with pytest.raises(hps.HpsError):
        hps.decode(b"NOT HALPST" + bytes(64))


def test_gcm_extract_wires(tmp_path):
    """A GameCube image sniffs as gcm and extract decodes its .hps to WAV."""
    from acidcat.core.infra import sniff as sniffmod
    from acidcat.core import samples as smod
    import wave, io

    hpsf = bytearray(b" HALPST\x00" + struct.pack(">II", 22050, 1) + bytes(0x38))
    blk = bytearray(0x20)
    struct.pack_into(">I", blk, 0, 8)
    struct.pack_into(">I", blk, 8, 0xFFFFFFFF)
    hpsf += blk + bytes([0x00, 0x21, 0, 0, 0, 0, 0, 0])
    img = tmp_path / "game.iso"
    img.write_bytes(_gcm_image("SONG.HPS", bytes(hpsf)))

    assert sniffmod.sniff(str(img)) == "gcm"
    recs = list(smod.iter_samples(str(img)))
    assert len(recs) == 1 and recs[0]["name"] == "SONG"
    w = wave.open(io.BytesIO(recs[0]["wav"]))
    assert w.getframerate() == 22050
