"""Creative Voice File, the sample format of the DOS era.

Every fixture here is generated, so a fresh clone can run the file. The shapes
they cover are not invented: each one is something the 896 real specimens in
DUKE3D.GRP and SW.GRP actually do, including the single malformed file among
them.

The three that would be easy to get wrong, and that a plausible-looking walker
gets wrong silently:

  the terminator is ONE byte      type 0 carries no length field, so a reader
                                  that reads four bytes for every block header
                                  walks off the end of nearly every file
  continuations carry no format   block 02 is samples and nothing else, so its
                                  rate belongs to the block before it; 84 of the
                                  896 specimens have more than one sound block
  the time constant is not round  tc 165 is 10989 Hz, not the 11025 the same
                                  sound carries when it is rewritten as block 09
"""

import os
import struct

import pytest

from acidcat.core.infra import sniff
from acidcat.core.walk import voc


# ── builders, shaped like the real files ────────────────────────────

def header(version=0x010A, header_size=26, checksum=None):
    if checksum is None:
        checksum = (~version + 0x1234) & 0xFFFF
    return (voc.MAGIC + struct.pack("<HHH", header_size, version, checksum))


def block(kind, body=b""):
    n = len(body)
    return bytes([kind, n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF]) + body


def sound01(samples, tc=165, pack=0):
    """The original sound block: a time constant and a codec byte."""
    return block(1, bytes([tc, pack]) + samples)


def sound09(samples, rate=11025, bits=8, ch=1, fmt=0):
    """v1.20's replacement, which states its rate outright."""
    return block(9, struct.pack("<IBBH", rate, bits, ch, fmt) + b"\0" * 4 + samples)


TERM = b"\x00"
TONE8 = bytes((0x80 + int(40 * (i % 32 - 16) / 16)) & 0xFF for i in range(512))
TONE16 = struct.pack("<256h", *[int(3000 * ((i % 32) - 16) / 16) for i in range(256)])


def write(tmp_path, *parts, name="s.voc"):
    p = tmp_path / name
    p.write_bytes(b"".join(parts))
    return str(p)


def snd(chunks):
    return [c for c in chunks if c["id"].startswith("snd")]


def field(chunk, name):
    for f in chunk["fields"]:
        if f.get("name") == name:
            return f.get("value")
    return None


# ── the two sound blocks ────────────────────────────────────────────

class TestTheTwoSoundBlocks:
    def test_block_01_states_its_rate_as_a_time_constant(self, tmp_path):
        c, w = voc.inspect_voc(write(tmp_path, header(), sound01(TONE8, tc=165), TERM))
        s = snd(c)
        assert len(s) == 1, c
        assert field(s[0], "sample_rate") == 10989, (
            "1000000/(256-165) is 10989; rounding it to 11025 would be tidier "
            "and would not be what the file says")
        assert field(s[0], "bits_per_sample") == 8
        assert field(s[0], "encoding") == "8-bit unsigned PCM"
        assert s[0]["size"] == len(TONE8)

    def test_block_09_states_its_rate_outright(self, tmp_path):
        c, w = voc.inspect_voc(write(tmp_path, header(0x0114), sound09(TONE8), TERM))
        s = snd(c)
        assert field(s[0], "sample_rate") == 11025
        assert field(s[0], "channels") == 1
        assert s[0]["size"] == len(TONE8)

    def test_sixteen_bit_is_signed_and_the_walker_says_so(self, tmp_path):
        """Format code 4. Three specimens carry it, and their samples sit around
        zero rather than around 0x80 -- which is the difference between audio
        and a loud DC thump."""
        c, w = voc.inspect_voc(
            write(tmp_path, header(0x0114), sound09(TONE16, bits=16, fmt=4), TERM))
        s = snd(c)
        assert field(s[0], "bits_per_sample") == 16
        assert field(s[0], "encoding") == "16-bit signed PCM"
        note = [f.get("note") for f in s[0]["fields"] if f.get("name") == "encoding"][0]
        assert note and "signed" in note and "centred on 0" in note, (
            "the NAME saying 'signed' is prose; the note is what carries the "
            "flag a decoder reads, and it was going untested: %r" % (note,))
        eight = voc.inspect_voc(write(tmp_path, header(), sound09(TONE8), TERM,
                                      name="e.voc"))[0]
        n8 = [f.get("note") for f in snd(eight)[0]["fields"]
              if f.get("name") == "encoding"][0]
        assert n8 and "0x80" in n8, n8

    def test_the_duration_uses_the_width_not_just_the_length(self, tmp_path):
        """A 16-bit stream holds half as many samples as an 8-bit one of the
        same size, so a duration computed from bytes alone is out by 2x."""
        eight = voc.inspect_voc(write(tmp_path, header(), sound09(TONE16), TERM))[0]
        sixteen = voc.inspect_voc(
            write(tmp_path, header(), sound09(TONE16, bits=16, fmt=4), TERM,
                  name="b.voc"))[0]
        d8 = field(snd(eight)[0], "duration")
        d16 = field(snd(sixteen)[0], "duration")
        assert d8 and d16 and d8 != d16, (d8, d16)


# ── continuation, the thing that breaks naive readers ───────────────

class TestContinuation:
    def test_a_continuation_extends_the_stream_before_it(self, tmp_path):
        """One stream, not two: block 02 is samples with no format of its own."""
        c, w = voc.inspect_voc(write(
            tmp_path, header(), sound01(TONE8), block(2, TONE8), block(2, TONE8), TERM))
        s = snd(c)
        assert len(s) == 1, f"{len(s)} streams; a continuation is not a new one"
        assert s[0]["size"] == len(TONE8) * 3
        assert field(s[0], "sample_rate") == 10989, "the rate came from block 01"

    def test_a_continuation_with_nothing_to_continue_is_reported(self, tmp_path):
        c, w = voc.inspect_voc(write(tmp_path, header(), block(2, TONE8), TERM))
        assert any("follows no sound block" in x for x in w), w

    def test_two_real_sound_blocks_are_two_streams(self, tmp_path):
        c, w = voc.inspect_voc(write(
            tmp_path, header(), sound01(TONE8), sound09(TONE8, rate=8000), TERM))
        s = snd(c)
        assert len(s) == 2
        assert field(s[0], "sample_rate") == 10989
        assert field(s[1], "sample_rate") == 8000


# ── framing ─────────────────────────────────────────────────────────

class TestFraming:
    def test_the_terminator_is_one_byte(self, tmp_path):
        """Type 0 has no length field. Reading four bytes for it walks past the
        end of the file, which is why a naive walk reports almost every real
        specimen as unterminated."""
        c, w = voc.inspect_voc(write(tmp_path, header(), sound01(TONE8), TERM))
        assert w == [], (
            "a well-formed file must produce NO complaint. Asserting only that "
            "the word 'terminator' is absent passes when mis-framing it raises "
            "some other warning instead, which is exactly what happens")
        assert not any(c2["warnings"] for c2 in c), [c2["warnings"] for c2 in c]

    def test_a_missing_terminator_is_a_warning_not_a_failure(self, tmp_path):
        c, w = voc.inspect_voc(write(tmp_path, header(), sound01(TONE8)))
        assert snd(c), "the audio is still readable"
        assert any("without a terminator" in x for x in w), w

    def test_text_blocks_are_carried(self, tmp_path):
        c, w = voc.inspect_voc(write(
            tmp_path, header(), block(5, b"recorded 1995\0"), sound01(TONE8), TERM))
        txt = [x for x in c if x["id"].startswith("txt")]
        assert txt and "recorded 1995" in txt[0]["summary"]

    def test_an_unknown_block_type_does_not_derail_the_chain(self, tmp_path):
        """Types 03, 04, 06, 07 and 08 appear in no specimen here. They are
        framed rather than understood, so the sound after one must still be
        found."""
        c, w = voc.inspect_voc(write(
            tmp_path, header(), block(4, b"\x01\x00"), sound01(TONE8), TERM))
        assert len(snd(c)) == 1, "a marker block swallowed the audio behind it"


# ── damage ──────────────────────────────────────────────────────────

class TestDamage:
    def test_a_length_past_the_end_reads_what_is_there(self, tmp_path):
        """The one malformed file in 896: a block 09 declaring 16 bytes with 13
        remaining. It must be reported and must not raise."""
        stub = header(0x0114) + block(9, struct.pack("<IBBH", 8000, 0, 1, 0) + b"\0" * 3)
        stub = stub[:-1]                       # cut it short, as the real one is
        p = tmp_path / "trunc.voc"
        p.write_bytes(stub)
        c, w = voc.inspect_voc(str(p))
        assert c, "returned nothing at all"
        assert any("only" in x and "remain" in x for x in w), w

    def test_zero_bits_does_not_divide_by_zero(self, tmp_path):
        c, w = voc.inspect_voc(write(
            tmp_path, header(0x0114), sound09(b"", bits=0, fmt=0), TERM))
        assert c
        assert any("no samples" in x for ch in c for x in ch["warnings"]), c

    def test_a_bad_checksum_is_reported_and_the_file_still_reads(self, tmp_path):
        c, w = voc.inspect_voc(write(
            tmp_path, header(checksum=0xDEAD), sound01(TONE8), TERM))
        assert snd(c), "the audio is fine; only the header field disagrees"
        assert any("checksum" in x for x in c[0]["warnings"]), c[0]["warnings"]

    def test_a_header_size_outside_the_file_falls_back(self, tmp_path):
        c, w = voc.inspect_voc(write(
            tmp_path, header(header_size=9999), sound01(TONE8), TERM))
        assert any("outside the file" in x for x in w), w

    def test_a_codec_that_is_not_pcm_is_named_not_decoded(self, tmp_path):
        """4-bit ADPCM played as linear PCM is noise. No specimen here carries
        it, so the walker names it and declines rather than guessing."""
        c, w = voc.inspect_voc(write(
            tmp_path, header(), sound01(TONE8, pack=1), TERM))
        s = snd(c)
        assert "ADPCM" in field(s[0], "encoding")
        assert any("not linear PCM" in x for x in s[0]["warnings"]), s[0]["warnings"]


# ── the namespace ───────────────────────────────────────────────────

class TestItIsWiredIn:
    def test_sniff_names_it(self, tmp_path):
        p = write(tmp_path, header(), sound01(TONE8), TERM)
        assert sniff.sniff(p) == "voc"
        with open(p, "rb") as fh:
            assert sniff.sniff_bytes(fh.read(32)) == "voc"

    def test_the_walker_is_registered(self):
        from acidcat.core import walk
        assert "voc" in walk._WALKERS
        assert "voc" in sniff.KNOWN_FORMATS

    def test_walk_file_reaches_it(self, tmp_path):
        from acidcat.core.walk import walk_file
        p = write(tmp_path, header(), sound01(TONE8), TERM)
        label, chunks, warns = walk_file(p, fmt_override="voc")
        assert "Creative Voice" in label, label
        assert any(c["id"].startswith("snd") for c in chunks), chunks

    def test_it_declines_what_is_not_a_voc(self, tmp_path):
        p = tmp_path / "no.voc"
        p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        c, w = voc.inspect_voc(str(p))
        assert c == [] and w, (c, w)

    def test_the_magic_is_twenty_bytes_including_the_eof_marker(self):
        """Nineteen characters then 0x1A. Every one of the 896 specimens has
        it, so the shorter prefix would be a weaker claim for no reason."""
        assert voc.MAGIC == b"Creative Voice File\x1a"
        assert len(voc.MAGIC) == 20


def test_the_rate_formula_is_the_dos_one():
    """Checked against the constants the corpus actually uses."""
    assert voc.rate_from_constant(165) == 10989
    assert voc.rate_from_constant(131) == 8000
    assert voc.rate_from_constant(89) == 5988
    assert voc.rate_from_constant(0) is None, "256-0 is not a rate, it is a bug"


def test_real_voc_corpus():
    """The 896 specimens, if the archives are on this machine.

    Opt-in by environment rather than by drive letter. Ground truth is derived
    from each archive's own index, so the test cannot drift into asserting
    whatever the walker happens to do.
    """
    root = os.environ.get("ACIDCAT_ARCHIVE_CORPUS", "")
    if not (root and os.path.isdir(root)):
        pytest.skip("set ACIDCAT_ARCHIVE_CORPUS to a dir of game archives")
    from acidcat.core.forensics import toc
    import tempfile
    seen = walked = 0
    out = tempfile.mkdtemp(prefix="voc-corpus-")
    for dirpath, _d, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(dirpath, name)
            try:
                with open(path, "rb") as fh:
                    if fh.read(12) != b"KenSilverman":
                        continue
                    fh.seek(0)
                    got = toc.read_toc(fh)
                    if not got:
                        continue
                    for e in got[1]:
                        if not e["name"].lower().endswith(".voc"):
                            continue
                        seen += 1
                        fh.seek(e["offset"])
                        p = os.path.join(out, "%d.voc" % seen)
                        with open(p, "wb") as w:
                            w.write(fh.read(e["length"]))
                        chunks, _fw = voc.inspect_voc(p)
                        assert chunks, f"{e['name']} produced no chunks"
                        walked += 1
            except OSError:
                continue
    if not seen:
        pytest.skip("no .grp archive with VOC entries under that directory")
    assert walked == seen, f"walked {walked} of {seen}"
