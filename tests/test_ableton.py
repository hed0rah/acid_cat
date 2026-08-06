"""Ableton .asd and the gzipped-XML family.

The .asd layout was reverse-engineered against 8,196 real specimens and checked
frame-for-frame against the source audio on 22 of them. The fixtures here are
generated so the suite runs on a clean checkout; the real-corpus check at the
bottom is opt-in via ACIDCAT_ABLETON_CORPUS.
"""

import gzip
import os
import struct
import zlib

import pytest

from acidcat.core.formats import ableton as ab
from acidcat.core.infra import sniff
from acidcat.core.walk import ableton as walker


def build_asd(frames, order="<", count=None, reserved=0, tail=b""):
    """A minimal but structurally honest .asd."""
    magic = ab.ASD_MAGIC_LE if order == "<" else ab.ASD_MAGIC_BE
    n = len(frames) + 1 if count is None else count
    body = struct.pack(f"{order}II", n, reserved)
    body += struct.pack(f"{order}{len(frames)}I", *frames)
    return magic + body + tail


def grid_for(rate, seconds, step=None):
    """A frame grid that steps at the 30 ms cap and ends on the frame count."""
    step = step or round(rate * ab.ASD_GRAIN_SECONDS)
    total = int(rate * seconds)
    out = list(range(step, total, step)) + [total]
    return out


# ── header ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("order,label", [("<", "little"), (">", "big")])
def test_both_byte_orders_parse(tmp_path, order, label):
    """06 'I' and 06 'M' are Intel/Motorola, the TIFF convention. 319 of the
    8,196 specimens were big-endian PowerPC-era Mac files, and they parse only
    big-endian."""
    raw = build_asd(grid_for(44100, 2.0), order=order)
    h = ab.parse_asd_header(raw)
    assert h["byte_order"] == label
    assert h["total_frames"] == 88200
    assert h["sample_rate"] == 44100


def test_last_grid_value_is_the_frame_count(tmp_path):
    raw = build_asd(grid_for(48000, 1.5))
    h = ab.parse_asd_header(raw)
    assert h["total_frames"] == 72000
    assert h["duration"] == pytest.approx(1.5)


def test_bad_magic_is_refused():
    with pytest.raises(ab.AbletonError):
        ab.parse_asd_header(b"\x07\x49" + b"\x00" * 32)


# ── sample-rate inference, and its honesty ────────────────────────────────

@pytest.mark.parametrize("rate", [22050, 44100, 48000, 96000])
def test_rate_is_read_off_the_30ms_cap(rate):
    h = ab.parse_asd_header(build_asd(grid_for(rate, 2.0)))
    assert h["sample_rate"] == rate
    assert h["rate_exact"] is True


def test_a_grid_that_never_reaches_the_cap_is_marked_inexact():
    """Steps below the cap only bound the rate from below. Reporting that as a
    reading would be the confident-wrong-answer bug this project keeps finding.
    """
    grid = grid_for(44100, 2.0, step=900)          # well under the 1323 cap
    h = ab.parse_asd_header(build_asd(grid))
    assert h["rate_exact"] is False
    assert h["sample_rate"] is not None            # still a usable lower bound


def test_an_impossible_step_infers_nothing_rather_than_guessing():
    grid = [0, 999_999]
    h = ab.parse_asd_header(build_asd(grid))
    assert h["sample_rate"] is None
    assert h["duration"] is None


# ── damaged input degrades, never crashes ─────────────────────────────────

def test_a_count_larger_than_the_file_is_flagged_not_fatal(tmp_path):
    raw = build_asd(grid_for(44100, 1.0), count=5_000_000)
    h = ab.parse_asd_header(raw)
    assert h["truncated"] is True
    assert h["frames"]                              # what was there is still read

    p = tmp_path / "t.wav.asd"
    p.write_bytes(raw)
    chunks, warns = walker.inspect_asd(str(p))
    assert chunks and any("holds only" in w for w in warns)


def test_a_non_monotonic_grid_is_flagged(tmp_path):
    raw = build_asd([100, 90, 200])
    p = tmp_path / "t.wav.asd"
    p.write_bytes(raw)
    _, warns = walker.inspect_asd(str(p))
    assert any("not strictly increasing" in w for w in warns)


def test_truncated_before_the_header():
    with pytest.raises(ab.AbletonError):
        ab.parse_asd_header(ab.ASD_MAGIC_LE + b"\x01")


# ── the AppleDouble impostor ──────────────────────────────────────────────

def test_appledouble_wearing_the_asd_extension_is_named(tmp_path):
    """129 of 8,196 files carrying .asd were macOS '._' resource stubs. Naming
    them beats reporting a corrupt Ableton file."""
    p = tmp_path / "._loop.wav.asd"
    p.write_bytes(ab.APPLEDOUBLE_MAGIC + b"\x00\x02\x00\x00Mac OS X" + b"\x00" * 64)
    chunks, warns = walker.inspect_asd(str(p))
    assert chunks[0]["id"] == "AppleDouble"
    assert any("AppleDouble" in w for w in warns)


def test_appledouble_is_not_sniffed_as_asd(tmp_path):
    """It must not enter the Ableton namespace at all -- classify already names
    it as a foreign file, which is more useful."""
    p = tmp_path / "._x.wav.asd"
    p.write_bytes(ab.APPLEDOUBLE_MAGIC + b"\x00" * 64)
    assert sniff.sniff(str(p)) != "asd"


# ── the sniffer must not be greedy: two magic bytes are weak ──────────────

def test_the_reserved_word_carries_the_confidence():
    """06 49 alone would fire on plenty of binaries; the zero u32 at offset 6
    is what makes the test safe."""
    assert ab.looks_like_asd(build_asd([100, 200])[:16]) is True
    bad = bytearray(build_asd([100, 200])[:16])
    bad[6] = 0xFF                                   # reserved no longer zero
    assert ab.looks_like_asd(bytes(bad)) is False


def test_zero_count_is_refused():
    assert ab.looks_like_asd(ab.ASD_MAGIC_LE + struct.pack("<II", 0, 0)) is False


def test_sniff_round_trip(tmp_path):
    p = tmp_path / "a.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0)))
    assert sniff.sniff(str(p)) == "asd"


# ── field names are UTF-16LE, which is why an ASCII scan misses them ──────

def test_field_names_need_their_length_prefix():
    name = "WarpMarkers"
    good = struct.pack("<I", len(name)) + name.encode("utf-16le")
    assert [n for _, n in ab.field_names(b"\x00\x00" + good)] == [name]
    # same text, no matching length prefix -> not a field
    bad = struct.pack("<I", 999) + name.encode("utf-16le")
    assert ab.field_names(b"\x00\x00" + bad) == []


def test_walker_reports_analysis_fields(tmp_path):
    body = b""
    for n in ("WarpMarkers", "IsWarped", "LoopStart", "OriginalFileSize"):
        body += struct.pack("<I", len(n)) + n.encode("utf-16le") + b"\x00\x04"
    p = tmp_path / "t.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=body))
    chunks, _ = walker.inspect_asd(str(p))
    objs = [c for c in chunks if c["id"] == "objects"][0]
    named = {f["name"] for f in objs["fields"]}
    assert {"WarpMarkers", "IsWarped", "LoopStart", "OriginalFileSize"} <= named


# ── the type dictionary ───────────────────────────────────────────────────

def _cls(name, n=0):
    return bytes([len(name)]) + name.encode("ascii") + struct.pack("<I", n)


def _fld(name, tag):
    return struct.pack("<I", len(name)) + name.encode("utf-16le") + bytes([tag])


def test_type_dictionary_reads_classes_and_typed_fields():
    blob = _cls("OnsetEvent", 3) + _fld("Time", 0x17) + _fld("Energy", 0x17) \
        + _fld("IsVolatile", 0x10)
    toks = ab.type_dictionary(blob, 0, len(blob))
    assert toks == [("class", "OnsetEvent", 3), ("field", "Time", 0x17),
                    ("field", "Energy", 0x17), ("field", "IsVolatile", 0x10)]


def test_the_tag_map_covers_the_types_seen_in_the_corpus():
    """Pinned by harvesting field->tag over 1,200 specimens: IsSet/IsVolatile
    are always 0x10, Version/ChannelCount always 0x11, and every 0x40 field has
    a plural name."""
    assert ab.TYPE_TAGS[0x10] == "bool"
    assert ab.TYPE_TAGS[0x11] == "int32"
    assert ab.TYPE_TAGS[0x40] == "list"


def test_an_unknown_tag_is_not_invented():
    """A tag we have not pinned must read as unknown rather than be guessed at
    -- 0x00 occurs in the corpus and its meaning is genuinely unsettled."""
    assert 0x00 not in ab.TYPE_TAGS


def test_field_names_follow_the_declared_byte_order():
    """A big-endian .asd stores UTF-16BE with a big-endian count. Reading those
    files as little-endian finds zero of their 64 field names, and the walker
    then reports "no analysis fields" -- which reads as a fact about the file
    rather than a fault in the reader. 319 of 8,196 specimens are big-endian.
    """
    n = "IsWarped"
    le = struct.pack("<I", len(n)) + n.encode("utf-16le")
    be = struct.pack(">I", len(n)) + n.encode("utf-16be")
    assert [x for _, x in ab.field_names(b"\x00\x00" + le, order="<")] == [n]
    assert [x for _, x in ab.field_names(b"\x00\x00" + be, order=">")] == [n]
    # and each must NOT read the other's encoding
    assert ab.field_names(b"\x00\x00" + be, order="<") == []
    assert ab.field_names(b"\x00\x00" + le, order=">") == []


def test_type_dictionary_reads_big_endian_files():
    name = "IsWarped"
    blob = (bytes([len("OnsetEvent")]) + b"OnsetEvent" + struct.pack(">I", 1)
            + struct.pack(">I", len(name)) + name.encode("utf-16be")
            + bytes([0x10]))
    toks = ab.type_dictionary(blob, 0, len(blob), order=">")
    assert toks == [("class", "OnsetEvent", 1), ("field", "IsWarped", 0x10)]


def test_a_big_endian_walk_reports_its_object_tree(tmp_path):
    """End to end: the bug was that header and grid parsed fine while the whole
    object tree silently vanished."""
    name = "IsWarped"
    tail = (struct.pack(">I", len(name)) + name.encode("utf-16be") + bytes([0x10]))
    p = tmp_path / "be.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), order=">", tail=tail))
    chunks, warns = walker.inspect_asd(str(p))
    objs = [c for c in chunks if c["id"] == "objects"]
    assert objs and "IsWarped" in {f["name"] for f in objs[0]["fields"]}
    assert not any("no recognised analysis fields" in w for w in warns)


def test_dictionary_walk_is_bounded():
    """The declarations sit at the front and the rest is the overview pyramid,
    so the walk must respect its end bound rather than scan the whole file."""
    blob = _cls("A_Class", 1) + b"\xff" * 4096
    assert ab.type_dictionary(blob, 0, 12) == [("class", "A_Class", 1)]


# ── the overview trailer ──────────────────────────────────────────────────

def _overview(channels, per_bin=None):
    per_bin = channels * 2 if per_bin is None else per_bin
    # 26 bytes sit between bytes_per_bin and the sentinel
    return (ab.OVERVIEW_MARK
            + struct.pack("<I", per_bin)          # sentinel-26
            + b"\x00" * 14
            + struct.pack("<I", channels)         # sentinel-8
            + b"\x00" * 4                         # sentinel-4
            + ab.OVERVIEW_SENTINEL)


@pytest.mark.parametrize("channels", [1, 2])
def test_overview_channel_count_is_read(channels):
    """Anchored on the sentinel, not on the class name: the trailer's length
    varies with the level count, so the name anchor agreed only ~79% of the
    time while the sentinel matched the source audio on 419 of 419 files."""
    ov = ab.overview_trailer(_overview(channels))
    assert ov["channels"] == channels
    assert ov["bytes_per_bin"] == channels * 2
    assert ov["consistent"] is True


def test_inconsistent_bytes_per_bin_is_flagged(tmp_path):
    body = _overview(2, per_bin=7)
    p = tmp_path / "t.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=body))
    _, warns = walker.inspect_asd(str(p))
    assert any("bytes_per_bin" in w for w in warns)


def test_no_overview_block_is_not_an_error():
    assert ab.overview_trailer(b"\x00" * 200) is None


# ── the gzipped-XML family ────────────────────────────────────────────────

def _gz(tmp_path, name, xml):
    p = tmp_path / name
    with gzip.open(p, "wb") as fh:
        fh.write(xml)
    return p


ROOT = b'<?xml version="1.0" encoding="UTF-8"?>\n<Ableton MajorVersion="5" ' \
       b'MinorVersion="12.0_12120" Creator="Ableton Live 12.1.5">\n'


def test_root_child_names_the_document_type(tmp_path):
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "a.als", ROOT + b"<LiveSet/>"))) == "als"
    assert ab.sniff_gzip_ableton(
        str(_gz(tmp_path, "b.adg", ROOT + b"<GroupDevicePreset/>"))) == "adg"
    # a device preset puts the DEVICE's class there, so it is the default case
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "c.adv", ROOT + b"<Operator/>"))) == "adv"


def test_set_and_clip_are_separated_only_by_extension(tmp_path):
    """Both use <LiveSet>; nothing in the content distinguishes them."""
    same = ROOT + b"<LiveSet/>"
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "x.als", same))) == "als"
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "x.alc", same))) == "alc"


def test_a_gzip_that_is_not_ableton_is_declined(tmp_path):
    """A Live Pack (.alp) is gzip too, but an archive rather than a document."""
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "p.alp", b"not xml at all"))) is None


def test_decompression_is_capped(tmp_path):
    """A Live Set expands ~20x in the wild; an uncapped read is a bomb vector."""
    p = tmp_path / "bomb.als"
    with gzip.open(p, "wb") as fh:
        fh.write(b"\x00" * (4 * 1024 * 1024))
    data, truncated = ab.gunzip_capped(str(p), cap=64 * 1024)
    assert len(data) == 64 * 1024 and truncated is True


def test_damaged_gzip_reports_rather_than_raising(tmp_path):
    p = tmp_path / "broken.als"
    p.write_bytes(b"\x1f\x8b\x08\x00" + b"\xff" * 400)
    chunks, warns = walker.inspect_ableton_xml(str(p))
    assert chunks == [] and warns


def test_xml_walker_reads_the_creator(tmp_path):
    p = _gz(tmp_path, "s.als", ROOT + b"<LiveSet><AudioTrack/><AudioTrack/>"
            b"<MidiTrack/></LiveSet>")
    chunks, _ = walker.inspect_ableton_xml(str(p), "als")
    attrs = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert attrs["Creator"] == "Ableton Live 12.1.5"
    assert "2 audio tracks" in chunks[1]["summary"]
    assert "1 MIDI tracks" in chunks[1]["summary"]


# ── Max for Live ──────────────────────────────────────────────────────────

def test_amxd_chunk_chain(tmp_path):
    """The chain starts at 12, after a constant 'aaaa' marker. Treating that
    marker as a chunk id makes its next 4 bytes read as a 1.6 GB length -- which
    is exactly how the first version of this walker failed on a real device."""
    p = tmp_path / "d.amxd"
    p.write_bytes(b"ampf" + struct.pack("<I", 4) + b"aaaa"
                  + b"meta" + struct.pack("<I", 4) + struct.pack("<I", 7)
                  + b"ptch" + struct.pack("<I", 6) + b"mx@c{}")
    chunks, warns = walker.inspect_amxd(str(p))
    assert [c["id"] for c in chunks] == ["ampf", "meta", "ptch"]
    assert not warns, warns
    assert "JSON at +4" in chunks[2]["summary"]


def test_amxd_lying_chunk_length_is_flagged(tmp_path):
    p = tmp_path / "bad.amxd"
    p.write_bytes(b"ampf" + struct.pack("<I", 4) + b"aaaa"
                  + b"meta" + struct.pack("<I", 0xFFFFFF) + b"aa")
    _, warns = walker.inspect_amxd(str(p))
    assert any("past end of file" in w for w in warns)


# ── opt-in: the real corpus ───────────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_ABLETON_CORPUS"),
                    reason="set ACIDCAT_ABLETON_CORPUS to a dir of real .asd files")
def test_real_corpus_parses():
    root = os.environ["ACIDCAT_ABLETON_CORPUS"]
    seen = ok = 0
    for dirpath, _, names in os.walk(root):
        for n in names:
            if not n.lower().endswith(".asd"):
                continue
            seen += 1
            raw = open(os.path.join(dirpath, n), "rb").read()
            if ab.is_appledouble(raw):
                continue
            h = ab.parse_asd_header(raw)
            assert h["monotonic"], n
            ok += 1
    assert seen and ok


def test_a_big_endian_file_without_an_overview_is_not_an_error():
    """Measured over 1,500 specimens: every big-endian .asd is the older
    generation and none carries an overview block, so the sentinel constant
    never needs byte-swapping. Absence here is ordinary -- the block is
    optional even in the newer generation (171 of 1,031)."""
    raw = build_asd(grid_for(44100, 1.0), order=">")
    assert ab.overview_trailer(raw, ">") is None
