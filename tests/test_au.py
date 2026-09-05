"""Sun / NeXT audio (.au / .snd), the self-describing form of early Unix PCM.

Every fixture is generated, so a fresh clone runs the file. The shapes covered
are the ones a plausible-looking walker gets wrong quietly:

  the size can be a sentinel      data_size 0xFFFFFFFF means "unknown", not a
                                  4 GB byte count; the real size is what follows
  eight bits is not always PCM    mu-law and A-law are eight bits on disk and
                                  companded, so they are a codec, not samples
  big-endian, not little          every field is Motorola byte order
  the header can carry a comment  the annotation sits between the fixed 24-byte
                                  header and the audio, sized by data_offset
"""

import os
import struct

from acidcat.core.infra import geometry, sniff
from acidcat.core.walk import au


# ── builder, shaped like a real .au ─────────────────────────────────

def au_file(encoding=3, rate=44100, channels=1, samples=b"", annot=b"",
            data_size=None, data_offset=None):
    """A well-formed Sun/NeXT audio file, with hooks to malform each field."""
    if data_offset is None:
        pad = (-len(annot)) % 4 if annot else 0            # keep audio 4-aligned
        annot = annot + b"\0" * pad if annot else annot
        data_offset = au._HDR_MIN + len(annot)
    if data_size is None:
        data_size = len(samples)
    hdr = au.MAGIC + struct.pack(">IIIII", data_offset, data_size,
                                 encoding, rate, channels)
    return hdr + annot + samples


def _one(tmp_path, data, name="s.au"):
    p = os.path.join(tmp_path, name)
    with open(p, "wb") as fh:
        fh.write(data)
    return p


def _chunk(chunks, cid):
    return next(c for c in chunks if c["id"] == cid)


def _field(chunk, name):
    return next(f for f in chunk["fields"] if f["name"] == name)


# ── sniffing ────────────────────────────────────────────────────────

def test_the_magic_is_recognised(tmp_path):
    p = _one(tmp_path, au_file(samples=b"\x00\x01" * 8))
    assert sniff.sniff_bytes(open(p, "rb").read(20)) == "au"
    assert sniff.sniff(p) == "au"


def test_an_mpc_snd_is_not_mistaken_for_this(tmp_path):
    # the MPC2000 .snd has no ".snd" magic; it must not sniff as au
    p = _one(tmp_path, b"\x01\x02name\x00" + b"\x00" * 40, name="drum.snd")
    assert sniff.sniff_bytes(open(p, "rb").read(20)) != "au"


# ── the fields ──────────────────────────────────────────────────────

def test_a_linear_pcm_file_reads_its_header(tmp_path):
    p = _one(tmp_path, au_file(encoding=3, rate=22050, channels=2,
                               samples=b"\x00\x01\x02\x03" * 16))
    chunks, warns = au.inspect_au(p)
    hdr = _chunk(chunks, "au")
    assert _field(hdr, "sample_rate")["value"] == 22050
    assert _field(hdr, "channels")["value"] == 2
    assert "16-bit linear PCM" in _field(hdr, "encoding")["value"]
    assert not hdr["warnings"]
    data = _chunk(chunks, "data")
    assert data["payload_base"] == au._HDR_MIN
    assert not data["warnings"]                            # linear PCM is clean


def test_mu_law_is_flagged_as_not_pcm(tmp_path):
    p = _one(tmp_path, au_file(encoding=1, rate=8000, samples=b"\x7f" * 64))
    chunks, _ = au.inspect_au(p)
    data = _chunk(chunks, "data")
    assert any("not linear PCM" in w for w in data["warnings"])


def test_the_unknown_size_sentinel_is_not_a_byte_count(tmp_path):
    body = b"\x00\x01" * 100
    p = _one(tmp_path, au_file(encoding=3, samples=body, data_size=au._UNKNOWN_SIZE))
    chunks, _ = au.inspect_au(p)
    hdr = _chunk(chunks, "au")
    assert "unknown" in _field(hdr, "data_size")["value"].lower()
    # the effective size is what actually follows the header, not 4 GB
    assert _chunk(chunks, "data")["size"] == len(body)


def test_an_annotation_is_read(tmp_path):
    p = _one(tmp_path, au_file(encoding=3, samples=b"\x00" * 8, annot=b"made by hand"))
    chunks, _ = au.inspect_au(p)
    hdr = _chunk(chunks, "au")
    assert _field(hdr, "annotation")["value"] == "made by hand"


def test_companded_audio_still_reports_a_duration(tmp_path):
    # mu-law and A-law are a fixed byte per sample on disk, so their duration is
    # exactly as computable as 8-bit linear PCM's. "not linear PCM" (the warning)
    # and "duration unknown" are different questions.
    for enc in (1, 27):
        p = _one(tmp_path, au_file(encoding=enc, rate=8000, samples=b"\x7f" * 8000),
                 name=f"{enc}.au")
        chunks, _ = au.inspect_au(p)
        data = _chunk(chunks, "data")
        dur = next((f["value"] for f in data["fields"] if f["name"] == "duration"), None)
        assert dur == "1.000 s", f"encoding {enc}: duration {dur!r}"
        assert any("not linear PCM" in w for w in data["warnings"])   # still flagged


# ── geometry: the header owns exactly its own bytes ─────────────────

def test_header_and_data_do_not_overlap(tmp_path):
    # the header has no RIFF-style 8-byte prefix, so it must declare its own
    # payload base. left to the default (offset + 8) it claims 32 bytes where 24
    # exist and overlaps the audio by 8 -- two siblings owning the same bytes,
    # which is what the geometry contract forbids.
    p = _one(tmp_path, au_file(encoding=3, samples=b"\x00\x01" * 100))
    chunks, _ = au.inspect_au(p)
    geometry.normalize(chunks, os.path.getsize(p))
    hdr = _chunk(chunks, "au")
    assert hdr["offset"] + hdr["extent_len"] == au._HDR_MIN        # 24, not 32
    spans = sorted((c["offset"], c["offset"] + c["extent_len"]) for c in chunks)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), spans
    assert all(geometry.is_trustworthy(c) for c in chunks)


# ── degrade, never raise ────────────────────────────────────────────

def test_a_truncated_header_warns_and_does_not_raise(tmp_path):
    p = _one(tmp_path, au_file()[:12])                     # magic + half the header
    chunks, _ = au.inspect_au(p)
    assert any("truncated" in w for c in chunks for w in c["warnings"])


def test_a_data_offset_past_the_file_is_a_warning(tmp_path):
    p = _one(tmp_path, au_file(samples=b"\x00" * 8, data_offset=1 << 20))
    chunks, _ = au.inspect_au(p)
    hdr = _chunk(chunks, "au")
    assert any("past the end" in w for w in hdr["warnings"])


def test_an_unknown_encoding_is_named_not_decoded(tmp_path):
    p = _one(tmp_path, au_file(encoding=99, samples=b"\x00" * 8))
    chunks, _ = au.inspect_au(p)
    hdr = _chunk(chunks, "au")
    assert any("encoding code 99" in w for w in hdr["warnings"])


def test_a_file_larger_than_the_header_window_still_sizes_its_audio(tmp_path):
    # audio geometry comes from the header and the file size, not the bounded
    # header read, so a file past _HEAD_CAP still reports its full data size.
    # this backs the SEARCH_WINDOW exemption of _HEAD_CAP in the cap ledger.
    body = b"\x00\x01" * (au._HEAD_CAP // 2 + 1000)        # > _HEAD_CAP
    p = _one(tmp_path, au_file(encoding=3, samples=body))
    chunks, _ = au.inspect_au(p)
    assert _chunk(chunks, "data")["size"] == len(body)


def test_a_huge_annotation_is_display_bounded(tmp_path):
    # the rendered annotation is capped; the audio geometry is unaffected. this
    # backs the SEARCH_WINDOW exemption of _ANNOT_CAP in the cap ledger.
    big = b"A" * (au._ANNOT_CAP * 3)
    p = _one(tmp_path, au_file(encoding=3, samples=b"\x00" * 8, annot=big))
    chunks, _ = au.inspect_au(p)
    val = _field(_chunk(chunks, "au"), "annotation")["value"]
    assert len(val) <= au._ANNOT_CAP


def test_a_non_au_file_is_declined(tmp_path):
    p = _one(tmp_path, b"RIFF____WAVE" + b"\x00" * 20, name="x.wav")
    chunks, warns = au.inspect_au(p)
    assert chunks == []
    assert warns and "not a Sun/NeXT audio" in warns[0]
