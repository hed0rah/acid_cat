"""Tests for the Commodore 64 SID walker (PSID / RSID).

The corpus these were written against is HVSC Update #85: 630 real tunes, all
of them PSID or RSID v2 and above. That leaves several paths with no real
specimen at all -- v1 headers, Compute!'s Sidplayer MUS data, tunes with more
than 32 subtunes, and every RSID constraint violation, since HVSC does not ship
files that break the spec. Those are built here, because a rule that is never
exercised is a rule nobody knows is wrong.
"""
import os
import struct

import pytest

from acidcat.core.formats import sid as sidmod
from acidcat.core.infra import sniff
from acidcat.core.walk import sid as walker


def _sid(magic=b"PSID", version=2, load=0, init=0x1000, play=0x1003,
         songs=1, start=1, speed=0, name="Test Tune", author="Tester",
         released="2026 acidcat", flags=0x0014, start_page=0, page_len=0,
         sid2=0, sid3=0, data=None, data_load=0x1000):
    """A structurally valid SID. Defaults are a plain PAL 6581 PSID v2."""
    off = sidmod.HEADER_V1 if version == 1 else sidmod.HEADER_V2
    hdr = bytearray()
    hdr += magic
    hdr += struct.pack(">HHHHHHH", version, off, load, init, play, songs, start)
    hdr += struct.pack(">I", speed)
    for s in (name, author, released):
        raw = s.encode("cp1252") if isinstance(s, str) else s
        hdr += raw[:32].ljust(32, b"\x00")
    if version >= 2:
        hdr += struct.pack(">H", flags)
        hdr += bytes([start_page, page_len, sid2, sid3])
    assert len(hdr) == off, (len(hdr), off)
    if data is None:
        # a C64 binary: little-endian load address, then some code
        data = struct.pack("<H", data_load) + bytes(range(0x20)) * 8
    return bytes(hdr) + data


# ── identification ──────────────────────────────────────────────────

def test_sniffs_psid_and_rsid(tmp_path):
    for magic in (b"PSID", b"RSID"):
        p = tmp_path / ("x_%s.sid" % magic.decode())
        p.write_bytes(_sid(magic=magic, version=2))
        assert sniff.sniff(str(p)) == "sid", magic


def test_a_version_outside_1_to_4_is_not_a_sid():
    """The magic alone would identify it; the version makes a false positive
    essentially impossible, and 'PSID' is four plausible ASCII bytes."""
    assert not sidmod.looks_like_sid(b"PSID" + struct.pack(">H", 9) + b"\x00\x7c")
    assert not sidmod.looks_like_sid(b"PSID" + struct.pack(">H", 0) + b"\x00\x7c")
    assert sidmod.looks_like_sid(b"PSID" + struct.pack(">H", 4) + b"\x00\x7c")


def test_sid_is_a_known_format():
    assert "sid" in sniff.KNOWN_FORMATS


# ── the byte-order trap ─────────────────────────────────────────────

def test_header_is_big_endian_and_the_data_is_not(tmp_path):
    """The one thing everybody gets wrong.

    Every multi-byte header field is Motorola order on a file describing a
    little-endian 6502, because the format was designed on the Amiga. The C64
    image after dataOffset is native, so the same file is read in both
    directions and the boundary is exactly at dataOffset.

    $1234 as a big-endian init and $CDAB as a little-endian load are chosen so
    a byte-order slip cannot coincidentally still pass.
    """
    p = tmp_path / "endian.sid"
    p.write_bytes(_sid(init=0x1234, data_load=0xCDAB))
    raw = p.read_bytes()

    assert raw[0x0A:0x0C] == b"\x12\x34", "init must be stored big-endian"
    assert raw[sidmod.HEADER_V2:sidmod.HEADER_V2 + 2] == b"\xab\xcd", \
        "the C64 load address must be stored little-endian"

    h = sidmod.parse_header(raw)
    assert h["init_address"] == 0x1234
    assert h["effective_load"] == 0xCDAB


# ── the load address ────────────────────────────────────────────────

def test_zero_load_address_means_read_it_from_the_data(tmp_path):
    """All 630 real tunes take this route, so it is the normal path."""
    p = tmp_path / "embedded.sid"
    p.write_bytes(_sid(load=0, data_load=0x0FF9))
    h = sidmod.parse_header(p.read_bytes())

    assert h["load_in_data"] is True
    assert h["effective_load"] == 0x0FF9
    # the two load-address bytes are not code, so the image starts after them
    assert h["code_offset"] == sidmod.HEADER_V2 + 2


def test_an_explicit_load_address_means_the_data_is_all_code(tmp_path):
    p = tmp_path / "explicit.sid"
    p.write_bytes(_sid(load=0x2000, data=bytes(64)))
    h = sidmod.parse_header(p.read_bytes())

    assert h["load_in_data"] is False
    assert h["effective_load"] == 0x2000
    assert h["code_offset"] == sidmod.HEADER_V2, \
        "with an explicit load address no bytes are consumed from the data"
    assert h["code_length"] == 64


def test_the_walker_reports_the_memory_extent(tmp_path):
    p = tmp_path / "extent.sid"
    p.write_bytes(_sid(load=0, data_load=0x1000,
                       data=struct.pack("<H", 0x1000) + bytes(0x100)))
    chunks, warns = walker.inspect_sid(str(p))
    data = [c for c in chunks if c["id"] == "data"][0]
    mem = [f for f in data["fields"] if f["name"] == "memory"][0]
    assert mem["value"] == "$1000-$10FF", mem["value"]


# ── header versions ─────────────────────────────────────────────────

def test_v1_header_is_0x76_and_has_no_flags(tmp_path):
    """No real specimen exists for this: HVSC re-saves everything as v2 or
    later, so all 630 measured tunes are v2+. The v1 path is spec-only."""
    p = tmp_path / "v1.sid"
    p.write_bytes(_sid(version=1))
    raw = p.read_bytes()
    h = sidmod.parse_header(raw)

    assert h["data_offset"] == 0x76
    assert h["has_v2_fields"] is False
    assert sidmod.violations(h, len(raw)) == []

    chunks, warns = walker.inspect_sid(str(p))
    names = {f["name"] for f in chunks[0]["fields"]}
    assert "flags" not in names, "a v1 header has no flags word to report"


def test_a_v2_header_declared_but_not_present_says_so(tmp_path):
    """Truncation between the v1 and v2 header sizes.

    The file claims v2, so the flags word should be at 0x76 -- and is not
    there. Reading it off the end and reporting the result would be inventing
    a fact about a file that does not state one.
    """
    p = tmp_path / "short.sid"
    p.write_bytes(_sid(version=2)[:0x78])
    chunks, warns = walker.inspect_sid(str(p))
    flags = [f for f in chunks[0]["fields"] if f["name"] == "flags"][0]
    assert flags["value"] == "not present", flags


# ── RSID: the constraints that are MUSTs ────────────────────────────

@pytest.mark.parametrize("kwargs,field", [
    ({"load": 0x1000}, "loadAddress"),
    ({"play": 0x1003}, "playAddress"),
    ({"speed": 0x00000001}, "speed"),
    ({"version": 1}, "version"),
])
def test_rsid_rejects_what_the_spec_says_it_must(kwargs, field):
    """These are not preferences. The format description says a reader must
    reject an RSID that breaks them, because those four fields are what force
    a tune to configure real hardware rather than lean on an emulator."""
    # an otherwise-conforming RSID, with exactly one field broken by the case
    base = {"version": 2, "load": 0, "init": 0x1000, "play": 0, "speed": 0}
    base.update(kwargs)
    raw = _sid(magic=b"RSID", **base)
    bad = sidmod.violations(sidmod.parse_header(raw), len(raw))
    assert any(f == field for f, _ in bad), (field, bad)


def test_rsid_may_not_load_below_07e8():
    raw = _sid(magic=b"RSID", play=0, speed=0, data_load=0x0400)
    bad = sidmod.violations(sidmod.parse_header(raw), len(raw))
    assert any("07E8" in c for _, c in bad), bad


def test_rsid_init_may_not_point_into_rom():
    """The RSID environment leaves the bank register at 0x37, so both ROMs are
    visible and an init there would execute ROM, not the tune."""
    for addr in (0xA000, 0xBFFF, 0xD000, 0xFFFF):
        raw = _sid(magic=b"RSID", init=addr, play=0, speed=0)
        bad = sidmod.violations(sidmod.parse_header(raw), len(raw))
        assert any(f == "initAddress" for f, _ in bad), (hex(addr), bad)


def test_the_c64_basic_flag_requires_a_zero_init():
    """Both real BASIC tunes in the corpus honour this: RSID v2, load $0801,
    init 0."""
    raw = _sid(magic=b"RSID", init=0x0900, play=0, speed=0,
               flags=0x0016, data_load=0x0801)
    bad = sidmod.violations(sidmod.parse_header(raw), len(raw))
    assert any("BASIC" in c for _, c in bad), bad

    ok = _sid(magic=b"RSID", init=0, play=0, speed=0,
              flags=0x0016, data_load=0x0801)
    assert sidmod.violations(sidmod.parse_header(ok), len(ok)) == []


def test_a_conforming_rsid_has_no_complaints():
    raw = _sid(magic=b"RSID", version=2, load=0, init=0x1000, play=0,
               speed=0, data_load=0x1000)
    assert sidmod.violations(sidmod.parse_header(raw), len(raw)) == []


# ── extra SID chips ─────────────────────────────────────────────────

def test_second_sid_address_decodes_to_the_dxx0_window():
    """The byte is the middle of $Dxx0: 0x42 is $D420. Both values below are
    in the corpus."""
    assert sidmod.sid_address(0x42) == 0xD420
    assert sidmod.sid_address(0x50) == 0xD500
    assert sidmod.sid_address(0xF0) == 0xDF00
    assert sidmod.sid_address(0xFE) == 0xDFE0


@pytest.mark.parametrize("value", [0x00, 0x41, 0x43, 0x80, 0xDF, 0xFF])
def test_illegal_extra_sid_positions_mean_no_chip(value):
    """Odd values and the two reserved ranges. 0x00-0x41 would collide with the
    first SID and the VIC-II; 0x80-0xDF lands in colour RAM and the CIAs. The
    spec's answer for all of them is the same: no extra SID."""
    assert sidmod.sid_address(value) is None


def test_third_sid_may_not_share_the_second_sid_address():
    raw = _sid(version=4, sid2=0x42, sid3=0x42)
    bad = sidmod.violations(sidmod.parse_header(raw), len(raw))
    assert any(f == "thirdSIDAddress" for f, _ in bad), bad


def test_an_extra_sid_field_set_below_its_version_is_flagged(tmp_path):
    """secondSIDAddress is a v3 field. A v2 header setting it is stating
    something its own version does not define."""
    p = tmp_path / "v2_with_sid2.sid"
    p.write_bytes(_sid(version=2, sid2=0x42))
    chunks, _ = walker.inspect_sid(str(p))
    f = [x for x in chunks[0]["fields"] if x["name"] == "secondSIDAddress"][0]
    assert "v3 specific" in f["note"] or "v2" in f["note"], f["note"]


# ── flags ───────────────────────────────────────────────────────────

def test_flags_decode_clock_and_chip_model():
    h = sidmod.parse_header(_sid(flags=0x0024))     # PAL, MOS8580
    assert sidmod.clock_name(h["clock"]) == "PAL"
    assert sidmod.model_name(h["sid_model"]) == "MOS8580"

    h = sidmod.parse_header(_sid(flags=0x0018))     # NTSC, MOS6581
    assert sidmod.clock_name(h["clock"]) == "NTSC"
    assert sidmod.model_name(h["sid_model"]) == "MOS6581"

    h = sidmod.parse_header(_sid(flags=0x0034))     # PAL, both models
    assert sidmod.model_name(h["sid_model"]) == "MOS6581 and MOS8580"


def test_reserved_flag_bits_are_a_violation():
    raw = _sid(flags=0x8000)
    bad = sidmod.violations(sidmod.parse_header(raw), len(raw))
    assert any(f == "flags" for f, _ in bad), bad


def test_the_mus_bit_says_there_is_no_player_in_the_file(tmp_path):
    """Compute!'s Sidplayer data. No real specimen in this corpus, but the bit
    changes what the payload IS: music data with no player, which cannot be
    replayed until an external player is merged with it."""
    p = tmp_path / "mus.sid"
    p.write_bytes(_sid(flags=0x0015))
    chunks, _ = walker.inspect_sid(str(p))
    data = [c for c in chunks if c["id"] == "data"][0]
    assert "MUS" in data["summary"], data["summary"]
    assert "merged" in data["summary"]


# ── the speed field ─────────────────────────────────────────────────

def test_speed_is_one_bit_per_subtune():
    h = sidmod.parse_header(_sid(songs=4, speed=0b0101))
    assert [sidmod.song_speed(h["speed"], n, 4, False, 2) for n in (1, 2, 3, 4)] \
        == ["CIA", "VBI", "CIA", "VBI"]


def test_past_32_subtunes_the_two_generations_disagree():
    """The one genuine fork in reading this field.

    v1, and v2+ with the PlaySID-specific flag set, WRAP: song 33 reuses bit 0.
    v2NG and later with that flag clear instead REPEAT bit 31 for everything
    above 32. Nothing below 33 subtunes can tell the two rules apart, which is
    why this is the case worth pinning.
    """
    speed = 1 << 0            # song 1 is CIA, song 32 (bit 31) is VBI
    assert sidmod.song_speed(speed, 33, 40, True, 2) == "CIA", "psidSpecific wraps"
    assert sidmod.song_speed(speed, 33, 40, False, 1) == "CIA", "v1 wraps"
    assert sidmod.song_speed(speed, 33, 40, False, 2) == "VBI", "v2NG clamps to bit 31"

    speed = 1 << 31           # now bit 31 is CIA and bit 0 is VBI
    assert sidmod.song_speed(speed, 33, 40, False, 2) == "CIA"
    assert sidmod.song_speed(speed, 33, 40, True, 2) == "VBI"


# ── strings ─────────────────────────────────────────────────────────

def test_a_full_32_byte_name_does_not_run_into_the_author(tmp_path):
    """37 of 1,890 string fields across 630 real tunes have no terminator.

    A C-string read that does not stop at 32 concatenates name and author.
    That is the classic way to read a SID header wrong, and it is silent: the
    result is a plausible string.
    """
    name = "A" * 32
    p = tmp_path / "full.sid"
    p.write_bytes(_sid(name=name, author="Bee"))
    h = sidmod.parse_header(p.read_bytes())

    assert h["name"] == name
    assert h["author"] == "Bee"
    assert h["name_terminated"] is False

    chunks, _ = walker.inspect_sid(str(p))
    f = [x for x in chunks[0]["fields"] if x["name"] == "name"][0]
    assert "no NUL" in f["note"], f["note"]


def test_strings_decode_as_windows_1252_not_latin_1():
    """They agree everywhere except 0x80-0x9F, where cp1252 has typographic
    characters and Latin-1 has C1 controls. 0x93 and 0x94 are curly quotes.
    """
    raw = _sid(name=b"\x93Quoted\x94")
    h = sidmod.parse_header(raw)
    assert h["name"] == "“Quoted”", repr(h["name"])


def test_an_undefined_cp1252_byte_still_decodes():
    """Five byte values are undefined in cp1252 and raise. Latin-1 backstops
    them because it maps every possible byte, so a hostile header cannot make
    text decoding throw."""
    assert sidmod.decode_text(b"\x81\x8d\x8f\x90\x9d")


# ── robustness ──────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [0, 1, 4, 8, 0x16, 0x40, 0x76, 0x7B, 0x7C, 0x7D])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    p = tmp_path / "trunc.sid"
    p.write_bytes(_sid()[:n])
    sidmod.parse_header(p.read_bytes())      # must not raise
    try:
        walker.inspect_sid(str(p))
    except Exception as exc:                 # noqa: BLE001 - that IS the assertion
        pytest.fail("truncation to %d bytes raised %r" % (n, exc))


def test_a_data_offset_past_the_end_is_reported_not_followed(tmp_path):
    raw = bytearray(_sid())
    struct.pack_into(">H", raw, 6, 0xFFFF)
    p = tmp_path / "liar.sid"
    p.write_bytes(bytes(raw))

    chunks, warns = walker.inspect_sid(str(p))
    assert any("past the end" in w for w in warns), warns
    assert [c for c in chunks if c["id"] == "data"] == [], \
        "no data chunk should be built from an offset outside the file"


def test_songs_and_startsong_are_range_checked():
    raw = _sid(songs=0)
    assert any(f == "songs" for f, _ in
               sidmod.violations(sidmod.parse_header(raw), len(raw)))
    raw = _sid(songs=4, start=9)
    assert any(f == "startSong" for f, _ in
               sidmod.violations(sidmod.parse_header(raw), len(raw)))


def test_page_length_must_be_zero_at_both_sentinel_start_pages():
    for page in (0x00, 0xFF):
        raw = _sid(start_page=page, page_len=3)
        bad = sidmod.violations(sidmod.parse_header(raw), len(raw))
        assert any(f == "pageLength" for f, _ in bad), (hex(page), bad)


# ── the songlength key ──────────────────────────────────────────────

def test_songlength_md5_is_the_hash_of_the_whole_file(tmp_path):
    """Since HVSC #71 the database key is plain MD5 over the entire file,
    header included. Verified against all 630 tunes of HVSC Update #85 against
    Songlengths.md5 (61,157 entries): 630 matched."""
    import hashlib
    blob = _sid()
    p = tmp_path / "hash.sid"
    p.write_bytes(blob)
    assert sidmod.songlength_md5(blob) == hashlib.md5(blob).hexdigest()

    chunks, _ = walker.inspect_sid(str(p))
    f = [x for x in chunks[0]["fields"] if x["name"] == "songlengthMD5"][0]
    assert f["value"] == hashlib.md5(blob).hexdigest()


# ── the whole walk ──────────────────────────────────────────────────

def test_the_walk_covers_every_byte(tmp_path):
    """Header plus data is the entire file, so a SID has no cavity by
    construction. Measured across 630 real tunes: all covered."""
    from acidcat.core.infra import geometry
    blob = _sid()
    p = tmp_path / "cover.sid"
    p.write_bytes(blob)
    chunks, _ = walker.inspect_sid(str(p))
    geometry.normalize(chunks, len(blob))

    covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                  for c in chunks)
    assert covered == len(blob), (covered, len(blob))
    assert all(geometry.is_trustworthy(c) for c in chunks)


# ── opt-in: the real corpus ─────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_SID_CORPUS"),
                    reason="set ACIDCAT_SID_CORPUS to a dir of real .sid files")
def test_real_corpus_walks_completely():
    """Every real tune must sniff, walk, and account for all of its bytes.

    Measured on HVSC Update #85: 630 tunes, all sniffed as sid, none raised,
    none produced untrustworthy geometry, and every file was covered end to
    end. Coverage rather than a crash count, because a walker that quietly
    stopped early would pass the second and fail the first.
    """
    import glob
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file

    root = os.environ["ACIDCAT_SID_CORPUS"]
    files = sorted(glob.glob(os.path.join(root, "**", "*.sid"), recursive=True))
    assert files, "no .sid files under %s" % root

    covered_fully = 0
    for path in files:
        assert sniff.sniff(path) == "sid", path
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                      for c in chunks)
        covered_fully += (covered == size)
    assert covered_fully == len(files), (
        "%d of %d tunes were not fully accounted for"
        % (len(files) - covered_fully, len(files)))
