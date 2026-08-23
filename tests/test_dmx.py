"""DMX digital sound, the format inside a Doom WAD's DS* lumps.

A format with no magic string at all: eight bytes of header over raw samples.
That makes identification the interesting part, and it is why these tests spend
more effort on what must NOT be claimed than on what must.

The corroboration is arithmetic rather than a signature. `03 00` as a format
field means nothing on its own -- it occurs constantly in ordinary binary --
but the header also has to agree with the file it sits in: eight plus the
declared count must be the length, exactly. That held for all 1,181 shipped
lumps and it is what keeps a two-byte pattern from claiming half the disk.
"""

import os
import struct

import pytest

from acidcat.core.infra import sniff
from acidcat.core.walk import dmx


def lump(samples, rate=11025, fmt=3, count=None):
    """A DMX lump. `count` overrides the declared length, to build damage."""
    n = len(samples) if count is None else count
    return struct.pack("<HHI", fmt, rate, n) + samples


TONE = bytes((0x80 + int(60 * ((i % 40) - 20) / 20)) & 0xFF for i in range(2000))
PADDED = bytes([TONE[0]]) * 16 + TONE + bytes([TONE[-1]]) * 16


def write(tmp_path, data, name="DSTEST.lmp"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def field(chunk, name):
    for f in chunk["fields"]:
        if f.get("name") == name:
            return f.get("value")
    return None


# ── reading one ─────────────────────────────────────────────────────

class TestReadingASound:
    def test_the_header_is_read(self, tmp_path):
        c, w = dmx.inspect_dmx(write(tmp_path, lump(TONE, rate=22050)))
        assert w == [], w
        assert field(c[0], "sample_rate") == 22050
        assert field(c[0], "bits_per_sample") == 8
        assert field(c[0], "channels") == 1
        assert field(c[0], "encoding") == "8-bit unsigned PCM"

    def test_the_count_is_bytes_after_the_header_not_samples(self, tmp_path):
        """The two are the same number for 8-bit mono, which is exactly why it
        is easy to write a reader that is right by accident and wrong the day a
        format grows a second channel. The lump length is the check."""
        data = lump(TONE)
        assert len(data) == 8 + len(TONE)
        c, w = dmx.inspect_dmx(write(tmp_path, data))
        assert field(c[0], "sample_count") == len(TONE)
        pcm = [x for x in c if x["id"] == "pcm"][0]
        assert pcm["offset"] == 8 and pcm["size"] == len(TONE)

    def test_the_rate_is_read_not_assumed(self, tmp_path):
        """Three quarters of the shipped corpus is not 11025, and the field
        holds values no one would guess: 12025, 17990, 16000."""
        for rate in (11025, 22050, 12025, 17990, 44100):
            c, _w = dmx.inspect_dmx(write(tmp_path, lump(TONE, rate=rate)))
            assert field(c[0], "sample_rate") == rate

    def test_the_duration_uses_the_rate(self, tmp_path):
        slow = dmx.inspect_dmx(write(tmp_path, lump(TONE, rate=11025)))[0]
        fast = dmx.inspect_dmx(write(tmp_path, lump(TONE, rate=22050),
                                     name="b.lmp"))[0]
        assert field(slow[0], "duration") != field(fast[0], "duration")


# ── padding is reported, never removed ──────────────────────────────

class TestPadding:
    def test_padding_is_reported_when_present(self, tmp_path):
        c, _w = dmx.inspect_dmx(write(tmp_path, lump(PADDED)))
        pcm = [x for x in c if x["id"] == "pcm"][0]
        assert field(pcm, "padded") == "yes"

    def test_and_its_absence_is_reported_too(self, tmp_path):
        c, _w = dmx.inspect_dmx(write(tmp_path, lump(TONE)))
        pcm = [x for x in c if x["id"] == "pcm"][0]
        assert field(pcm, "padded") == "no"

    def test_padding_is_never_trimmed_from_the_samples(self, tmp_path):
        """168 of 1,181 shipped lumps have no lead-in. Subtracting a padding
        that is assumed rather than present cuts real audio off every one of
        them, and the cut is silent."""
        c, _w = dmx.inspect_dmx(write(tmp_path, lump(PADDED)))
        pcm = [x for x in c if x["id"] == "pcm"][0]
        assert pcm["offset"] == 8, "samples must start at the header end"
        assert pcm["size"] == len(PADDED), (
            "the padding was trimmed out of the extent; it is the writer's "
            "habit, not the format's, and a reader cannot know which")


# ── what must not be claimed ────────────────────────────────────────

class TestIdentification:
    def test_the_size_rule_is_what_makes_the_claim_safe(self, tmp_path):
        """`03 00` plus a plausible rate is not an identification. The header
        must also agree with the file's own length."""
        good = lump(TONE)
        assert dmx.looks_like_dmx(good, len(good))
        assert not dmx.looks_like_dmx(good, len(good) + 1), (
            "a header that disagrees with its file was accepted")
        assert not dmx.looks_like_dmx(good[:-100], len(good) - 100)

    def test_a_plausible_header_over_the_wrong_length_is_refused(self, tmp_path):
        p = write(tmp_path, lump(TONE, count=999999))
        c, w = dmx.inspect_dmx(p)
        assert c == [] and w, "claimed a lump whose count does not fit"
        assert sniff.sniff(p) != "dmx"

    def test_an_implausible_rate_is_refused(self, tmp_path):
        """The field is a u16, so it cannot exceed 65535 whatever the bytes say
        -- the ceiling only has work to do between the highest rate anyone
        shipped and that hard limit."""
        for rate in (0, 1, 200, 3999, 48001, 65535):
            data = lump(TONE, rate=rate)
            assert not dmx.looks_like_dmx(data, len(data)), rate
        for rate in (4000, 11025, 48000):
            data = lump(TONE, rate=rate)
            assert dmx.looks_like_dmx(data, len(data)), rate

    def test_a_wrong_format_code_is_refused(self, tmp_path):
        for fmt in (0, 1, 2, 4, 255):
            data = lump(TONE, fmt=fmt)
            assert not dmx.looks_like_dmx(data, len(data)), fmt

    def test_a_wav_is_not_a_dmx(self, tmp_path):
        p = tmp_path / "x.wav"
        p.write_bytes(b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + bytes(28))
        assert sniff.sniff(str(p)) != "dmx"

    def test_random_bytes_of_the_right_length_are_refused(self, tmp_path):
        """The size rule is arithmetic, so it can be satisfied by chance. It
        takes a matching format code and a plausible rate as well, and that
        conjunction is what makes chance negligible."""
        import random
        random.seed(11)
        claimed = 0
        for _ in range(2000):
            n = random.randrange(64, 4096)
            b = bytes(random.randrange(256) for _ in range(8)) + bytes(n)
            if dmx.looks_like_dmx(b, len(b)):
                claimed += 1
        assert claimed == 0, f"{claimed} of 2000 random headers claimed"


# ── damage ──────────────────────────────────────────────────────────

class TestDamage:
    def test_a_truncated_lump_is_refused_rather_than_half_read(self, tmp_path):
        data = lump(TONE)[:20]
        c, w = dmx.inspect_dmx(write(tmp_path, data))
        assert c == [] and w

    def test_a_header_with_no_samples(self, tmp_path):
        c, w = dmx.inspect_dmx(write(tmp_path, lump(b"")))
        assert c, "a zero-length sound is still a readable header"
        assert any("no samples" in x for x in c[0]["warnings"]), c[0]["warnings"]

    def test_shorter_than_a_header(self, tmp_path):
        c, w = dmx.inspect_dmx(write(tmp_path, b"\x03\x00"))
        assert c == [] and w


# ── wiring ──────────────────────────────────────────────────────────

class TestItIsWiredIn:
    def test_sniff_names_it(self, tmp_path):
        assert sniff.sniff(write(tmp_path, lump(TONE))) == "dmx"

    def test_sniff_bytes_cannot_and_does_not_claim_it(self, tmp_path):
        """Deliberate. The corroboration needs the file's length and
        `sniff_bytes` only ever sees the head, so claiming it there would be a
        guess wearing an identification's clothes."""
        assert sniff.sniff_bytes(lump(TONE)[:20]) != "dmx"

    def test_the_walker_is_registered(self):
        from acidcat.core import walk
        assert "dmx" in walk._WALKERS
        assert "dmx" in sniff.KNOWN_FORMATS

    def test_walk_file_reaches_it(self, tmp_path):
        from acidcat.core.walk import walk_file
        label, chunks, warns = walk_file(write(tmp_path, lump(TONE)))
        assert "DMX" in label, label
        assert any(c["id"] == "pcm" for c in chunks), chunks


def test_real_doom_corpus():
    """Every DS* lump in every WAD under ACIDCAT_ARCHIVE_CORPUS.

    Ground truth comes from each WAD's own directory, so the test cannot drift
    into asserting whatever the walker happens to do.
    """
    root = os.environ.get("ACIDCAT_ARCHIVE_CORPUS", "")
    if not (root and os.path.isdir(root)):
        pytest.skip("set ACIDCAT_ARCHIVE_CORPUS to a dir of WADs")
    from acidcat.core.forensics import toc
    import tempfile
    out = tempfile.mkdtemp(prefix="dmx-corpus-")
    seen = 0
    for dirpath, _d, files in os.walk(root):
        for name in sorted(files):
            if not name.lower().endswith(".wad"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "rb") as fh:
                    if fh.read(4) not in (b"IWAD", b"PWAD"):
                        continue
                    fh.seek(0)
                    got = toc.read_toc(fh)
                    if not got or got[2] is None:
                        continue
                    for e in got[1]:
                        if not e["name"].startswith("DS"):
                            continue
                        seen += 1
                        fh.seek(e["offset"])
                        p = os.path.join(out, "%06d.lmp" % seen)
                        with open(p, "wb") as w:
                            w.write(fh.read(e["length"]))
                        chunks, fw = dmx.inspect_dmx(p)
                        assert chunks, f"{e['name']} in {name} produced nothing"
                        assert fw == [], f"{e['name']} in {name}: {fw}"
                        assert sniff.sniff(p) == "dmx", e["name"]
            except OSError:
                continue
    if not seen:
        pytest.skip("no WAD with DS* lumps under that directory")
