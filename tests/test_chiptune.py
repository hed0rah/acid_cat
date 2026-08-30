"""Tests for the chiptune containers: NSF, NSFe and SAP.

A caveat that belongs at the top rather than buried, because it changes what a
green run here means: THERE ARE NO NSF OR SAP SPECIMENS. Every fixture below is
synthetic, built from the specification. So these tests prove the walker agrees
with my reading of the spec -- and if I misread a field, the fixture encodes the
same misreading and the test passes anyway. That is a check which cannot fail on
the input it is given, which is the exact bug class this repo keeps finding.

What that means in practice: the assertions here are about STRUCTURE the spec
states unambiguously (offsets, widths, the inclusive end address, the length
order in an NSFe chunk header) rather than about values only a corpus could
settle. Where the specification itself is single-source or disputed, the test
says so rather than pinning a number that might be wrong.

Sources: NESdev wiki, Kevin Horton's nsfspec.txt, Disch's NSFe Revision 2,
asap.sourceforge.net. No GPL player source was consulted.
"""
import os
import struct

import pytest

from acidcat.core.infra import geometry, sniff
from acidcat.core.walk import chiptune


# ── builders ────────────────────────────────────────────────────────

def _nsf(ver=1, songs=3, start=1, load=0x8000, init=0x8003, play=0x8006,
         title=b"Test Tune", artist=b"Artist", copyright=b"2026",
         chips=0x00, region=0x00, banks=bytes(8), nsf2flags=0x00,
         data_len=0, body=b"\xea" * 64, ntsc=16666, pal=20000):
    h = bytearray(0x80)
    h[0:5] = b"NESM\x1a"
    h[5], h[6], h[7] = ver, songs, start
    struct.pack_into("<HHH", h, 8, load, init, play)
    h[0x0E:0x0E + len(title)] = title
    h[0x2E:0x2E + len(artist)] = artist
    h[0x4E:0x4E + len(copyright)] = copyright
    struct.pack_into("<H", h, 0x6E, ntsc)
    h[0x70:0x78] = banks
    struct.pack_into("<H", h, 0x78, pal)
    h[0x7A], h[0x7B], h[0x7C] = region, chips, nsf2flags
    h[0x7D] = data_len & 0xFF
    h[0x7E] = (data_len >> 8) & 0xFF
    h[0x7F] = (data_len >> 16) & 0xFF
    return bytes(h) + body


def _chunk(fourcc, data):
    """An NSFe chunk: LENGTH FIRST, then the FourCC. Reverse of RIFF."""
    return struct.pack("<I", len(data)) + fourcc + data


def _info(load=0x8000, init=0x8003, play=0x8006, region=0, chips=0,
          songs=3, start=0):
    return (struct.pack("<HHH", load, init, play)
            + bytes([region, chips, songs, start]))


def _nsfe(chunks=None):
    if chunks is None:
        chunks = [_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea" * 32),
                  _chunk(b"NEND", b"")]
    return b"NSFE" + b"".join(chunks)


def _sap(tags=None, blocks=((0x0F80, b"\xea" * 16),), eol="\r\n", ffff=True):
    if tags is None:
        tags = ['AUTHOR "Jakub Husak"', 'NAME "Inside"', 'DATE "1990"',
                "SONGS 1", "TYPE B", "INIT 0F80", "PLAYER 247F", "TIME 06:37.62"]
    head = "SAP" + eol + "".join(t + eol for t in tags)
    body = b"\xff\xff" if ffff else b""
    for start, data in blocks:
        body += struct.pack("<HH", start, start + len(data) - 1) + data
    return head.encode("latin-1") + body


def _w(tmp_path, name, blob):
    p = tmp_path / name
    p.write_bytes(blob)
    return str(p)


ALL = [("nsf", _nsf, chiptune.inspect_nsf),
       ("nsfe", _nsfe, chiptune.inspect_nsfe),
       ("sap", _sap, chiptune.inspect_sap)]


# ── the three, held to the same contract ────────────────────────────

@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
def test_each_is_identified_and_walked(tmp_path, fmt, build, walk):
    path = _w(tmp_path, "t." + fmt, build())
    assert sniff.sniff(path) == fmt
    chunks, _warns = walk(path)
    assert chunks


@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
def test_every_byte_is_accounted_for(tmp_path, fmt, build, walk):
    blob = build()
    path = _w(tmp_path, "t." + fmt, blob)
    chunks, _warns = walk(path)
    geometry.normalize(chunks, len(blob))
    assert all(geometry.is_trustworthy(c) for c in chunks)
    covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                  for c in chunks)
    assert covered == len(blob), (covered, len(blob))


@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
@pytest.mark.parametrize("n", [0, 4, 8, 64, 127, 128])
def test_truncation_at_any_depth_does_not_raise(tmp_path, fmt, build, walk, n):
    path = _w(tmp_path, "cut." + fmt, build()[:n])
    try:
        walk(path)
    except Exception as exc:               # noqa: BLE001 - that IS the assertion
        pytest.fail("%s truncated to %d bytes raised %r" % (fmt, n, exc))


# ── NSF: the fields that do not mean what they look like ────────────

def test_the_load_address_stops_being_an_address_when_banked(tmp_path):
    """The one genuine trap in the NSF header. With any bank byte non-zero the
    low 12 bits of the load address are a count of padding bytes at the start of
    the ROM, not a place to put it. There is no flag saying so -- the all-zero
    bank array IS the flag."""
    path = _w(tmp_path, "b.nsf", _nsf(load=0x8123, banks=bytes([0, 1, 2, 3, 4, 5, 6, 7])))
    chunks, _ = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["romPadding"] == "291", "0x123 = 291 bytes of padding"


def test_an_unbanked_file_reports_no_padding(tmp_path):
    """The control. romPadding appearing on a file with an all-zero bank array
    would mean the walker read a pad count out of a plain address."""
    path = _w(tmp_path, "u.nsf", _nsf(load=0x8123, banks=bytes(8)))
    chunks, _ = chiptune.inspect_nsf(path)
    assert "romPadding" not in {f["name"] for f in chunks[0]["fields"]}


def test_string_slots_are_fixed_width_not_nul_scanned(tmp_path):
    """A slot is always 32 bytes; the NUL ends the text inside it. A walker that
    scans for the NUL instead of capping at the slot runs a 32-non-NUL title
    straight into the artist field."""
    path = _w(tmp_path, "s.nsf", _nsf(title=b"A" * 32, artist=b"Artist"))
    chunks, warns = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["title"] == "A" * 32, "title must stop at the slot boundary"
    assert got["artist"] == "Artist", "the artist slot must be unaffected"
    assert any("no terminator" in w for w in warns)


def test_dirty_padding_after_the_terminator_is_reported(tmp_path):
    """Rippers leave remnants of a previous string after the NUL. Legal, and
    forensically interesting, so it is said rather than silently trimmed."""
    title = b"Short\x00LEFTOVER"
    path = _w(tmp_path, "d.nsf", _nsf(title=title))
    chunks, _ = chiptune.inspect_nsf(path)
    note = [f["note"] for f in chunks[0]["fields"] if f["name"] == "title"][0]
    assert "after the terminator" in note


@pytest.mark.parametrize("bit,name", [(0, "VRC6"), (1, "VRC7"), (2, "FDS"),
                                      (3, "MMC5"), (4, "Namco 163"),
                                      (5, "Sunsoft 5B")])
def test_each_expansion_chip_bit(tmp_path, bit, name):
    path = _w(tmp_path, "c.nsf", _nsf(chips=1 << bit))
    chunks, _ = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["expansion"] == name


def test_multiple_expansion_chips_are_all_named(tmp_path):
    path = _w(tmp_path, "m.nsf", _nsf(chips=0x01 | 0x04))
    chunks, _ = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["expansion"] == "VRC6, FDS"


def test_an_fds_load_below_8000_is_not_flagged_but_a_plain_one_is(tmp_path):
    """FDS rips legitimately load below $8000 to fill $6000-$7FFF. The same
    address without the FDS bit is suspect. A check that fires on both, or on
    neither, would be telling the reader nothing."""
    fds = _w(tmp_path, "fds.nsf", _nsf(load=0x6000, chips=0x04))
    plain = _w(tmp_path, "plain.nsf", _nsf(load=0x6000, chips=0x00))
    _c, fds_warns = chiptune.inspect_nsf(fds)
    _c, plain_warns = chiptune.inspect_nsf(plain)
    assert not any("below $8000" in w for w in fds_warns), fds_warns
    assert any("below $8000" in w for w in plain_warns), plain_warns


def test_version_1_reserved_byte_is_reported_not_parsed(tmp_path):
    """$7C is NSF2 feature flags in a v2 file and reserved in a v1 file. The
    spec says ignore it when version is 1 -- so it must not be decoded as flags,
    but a non-zero reserved byte is still a fingerprint worth surfacing."""
    path = _w(tmp_path, "r.nsf", _nsf(ver=1, nsf2flags=0x90))
    chunks, _ = chiptune.inspect_nsf(path)
    names = {f["name"] for f in chunks[0]["fields"]}
    assert "reserved" in names and "nsf2Flags" not in names


def test_version_2_decodes_the_same_byte_as_flags(tmp_path):
    """The control for the above: same byte, different version, different
    meaning."""
    path = _w(tmp_path, "v2.nsf", _nsf(ver=2, nsf2flags=0x90))
    chunks, _ = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert "IRQ" in got["nsf2Flags"] and "mandatory metadata" in got["nsf2Flags"]


def test_a_version_beyond_2_is_corruption_not_a_newer_format(tmp_path):
    """NSF has only ever had version bytes 1 and 2. There is no 1.01/1.02/1.03
    ladder -- that is PSID, a different format this repo also reads. So a high
    version byte means damage, and being permissive about it would be wrong."""
    path = _w(tmp_path, "v9.nsf", _nsf(ver=9))
    _chunks, warns = chiptune.inspect_nsf(path)
    assert any("only 1 and 2" in w for w in warns), warns


def test_a_declared_data_length_past_the_end_is_refused(tmp_path):
    path = _w(tmp_path, "long.nsf", _nsf(data_len=0xFFFFFF))
    chunks, warns = chiptune.inspect_nsf(path)
    assert any("past the end" in w for w in warns), warns
    size = os.path.getsize(path)
    geometry.normalize(chunks, size)
    assert all(geometry.is_trustworthy(c) for c in chunks)


def test_nsf2_mandatory_metadata_with_no_length_is_a_contradiction(tmp_path):
    path = _w(tmp_path, "x.nsf", _nsf(ver=2, nsf2flags=0x80, data_len=0))
    _chunks, warns = chiptune.inspect_nsf(path)
    assert any("no stated boundary" in w for w in warns), warns


def test_all_unknown_strings_is_a_bare_rip_not_damage(tmp_path):
    path = _w(tmp_path, "q.nsf", _nsf(title=b"<?>", artist=b"<?>", copyright=b"<?>"))
    _chunks, warns = chiptune.inspect_nsf(path)
    assert any("bare rip" in w for w in warns), warns


def test_a_zero_speed_word_is_only_flagged_for_the_declared_region(tmp_path):
    """A PAL speed of zero on an NTSC-only tune is harmless: nothing reads it.
    Flagging it anyway trains the reader to ignore the warning."""
    ntsc = _w(tmp_path, "n.nsf", _nsf(region=0x00, pal=0))
    palf = _w(tmp_path, "p.nsf", _nsf(region=0x01, pal=0))
    _c, ntsc_warns = chiptune.inspect_nsf(ntsc)
    _c, pal_warns = chiptune.inspect_nsf(palf)
    assert not any("PAL speed" in w for w in ntsc_warns), ntsc_warns
    assert any("PAL speed" in w for w in pal_warns), pal_warns


# ── NSFe: length before FourCC, and capitalisation carries meaning ──

def test_chunk_length_precedes_the_fourcc(tmp_path):
    """The NSFe trap. RIFF and IFF put the ID first; NSFe puts the length first.
    Plumbing borrowed from either reads a FourCC as a size and walks into
    nothing. This asserts the walker got the order right by giving DATA a length
    that would be absurd if read as an ID."""
    blob = _nsfe([_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea" * 300),
                  _chunk(b"NEND", b"")])
    path = _w(tmp_path, "o.nsfe", blob)
    chunks, warns = chiptune.inspect_nsfe(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["DATA"] == "300 bytes"
    assert not warns


def test_an_unknown_capitalised_chunk_is_surfaced_loudly(tmp_path):
    """Mandatoriness is encoded in the FIRST BYTE'S CASE. A-Z means a player
    that does not understand the chunk must refuse the file, so an unknown one
    is the format saying "you do not understand me"."""
    blob = _nsfe([_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea"),
                  _chunk(b"ZZZZ", b"x"), _chunk(b"NEND", b"")])
    path = _w(tmp_path, "z.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert any("MANDATORY" in w and "ZZZZ" in w for w in warns), warns


def test_an_unknown_lowercase_chunk_is_skippable_and_quiet(tmp_path):
    """The control that makes the case rule mean something. Same unknown chunk,
    lowercase initial, no complaint."""
    blob = _nsfe([_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea"),
                  _chunk(b"zzzz", b"x"), _chunk(b"NEND", b"")])
    path = _w(tmp_path, "l.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert not any("MANDATORY" in w for w in warns), warns


def test_a_chunk_length_past_the_end_stops_the_walk(tmp_path):
    """A 32-bit length can claim 4 GB. This is the primary structural check and
    the primary denial-of-service vector."""
    blob = b"NSFE" + struct.pack("<I", 0x7FFFFFFF) + b"DATA" + b"\xea" * 8
    path = _w(tmp_path, "big.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert any("runs past the end" in w for w in warns), warns


def test_missing_required_chunks_are_named(tmp_path):
    path = _w(tmp_path, "bare.nsfe", _nsfe([_chunk(b"auth", b"x\x00")]))
    _chunks, warns = chiptune.inspect_nsfe(path)
    for need in ("INFO", "DATA", "NEND"):
        assert any(need in w for w in warns), (need, warns)


def test_data_before_info_is_flagged(tmp_path):
    blob = _nsfe([_chunk(b"DATA", b"\xea"), _chunk(b"INFO", _info()),
                  _chunk(b"NEND", b"")])
    path = _w(tmp_path, "ord.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert any("before INFO" in w for w in warns), warns


def test_bytes_after_nend_are_reported(tmp_path):
    blob = _nsfe([_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea"),
                  _chunk(b"NEND", b"")]) + b"junkjunk"
    path = _w(tmp_path, "tail.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert any("follow NEND" in w for w in warns), warns


# ── SAP: text then a self-describing binary ─────────────────────────

def test_the_block_end_address_is_inclusive(tmp_path):
    """`end - start` loses the last byte of every block in the file, and the
    file still parses, so nothing complains. 0x0F80..0x0F8F is 16 bytes."""
    path = _w(tmp_path, "i.sap", _sap(blocks=((0x0F80, b"\xea" * 16),)))
    chunks, _ = chiptune.inspect_sap(path)
    blk = [f for f in chunks[1]["fields"] if f["name"] == "block 1"][0]
    assert blk["value"] == "$0F80-$0F8F"
    assert "16 bytes" in blk["note"]


def test_the_text_header_ends_at_the_first_ff_ff(tmp_path):
    """The spec defines NO end-of-header marker. The boundary is derived: the
    permitted character set tops out at 0x7C so 0xFF cannot occur in the header,
    and the binary half must open FF FF."""
    blob = _sap()
    path = _w(tmp_path, "b.sap", blob)
    chunks, _ = chiptune.inspect_sap(path)
    assert chunks[1]["offset"] == blob.index(b"\xff\xff")


def test_a_sap_with_no_binary_half_says_so(tmp_path):
    path = _w(tmp_path, "n.sap", _sap(blocks=(), ffff=False))
    chunks, warns = chiptune.inspect_sap(path)
    assert len(chunks) == 1
    assert any("never begins" in w for w in warns), warns


@pytest.mark.parametrize("typ,desc", [("B", "standard"), ("C", "Chaos"),
                                      ("D", "digitised"), ("S", "SoftSynth"),
                                      ("R", "raw POKEY")])
def test_every_defined_player_type_is_known(tmp_path, typ, desc):
    tags = ['AUTHOR "x"', "TYPE " + typ, "SONGS 1", "TIME 01:00"]
    tags.append("MUSIC 2000" if typ == "C" else "INIT 0F80")
    if typ in ("B", "C"):
        tags.append("PLAYER 3000")
    path = _w(tmp_path, "t.sap", _sap(tags=tags))
    chunks, _warns = chiptune.inspect_sap(path)
    note = [f for f in chunks[0]["fields"] if f["name"] == "type"][0]["note"]
    assert desc in note


def test_type_c_must_not_carry_init(tmp_path):
    """The spec says INIT is INVALID for type C, not merely unnecessary."""
    tags = ['AUTHOR "x"', "TYPE C", "MUSIC 2000", "INIT 0F80", "SONGS 1",
            "TIME 01:00"]
    path = _w(tmp_path, "c.sap", _sap(tags=tags))
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("must not carry INIT" in w for w in warns), warns


def test_type_b_requires_init(tmp_path):
    tags = ['AUTHOR "x"', "TYPE B", "PLAYER 3000", "SONGS 1", "TIME 01:00"]
    path = _w(tmp_path, "nb.sap", _sap(tags=tags))
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("requires INIT" in w for w in warns), warns


def test_one_time_line_per_subsong(tmp_path):
    tags = ['AUTHOR "x"', "TYPE B", "INIT 0F80", "PLAYER 3000", "SONGS 3",
            "TIME 01:00", "TIME 02:00"]
    path = _w(tmp_path, "tm.sap", _sap(tags=tags))
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("2 TIME line(s) for 3 song(s)" in w for w in warns), warns


def test_a_block_ending_before_it_starts_is_refused(tmp_path):
    head = b"SAP\r\nAUTHOR \"x\"\r\nTYPE B\r\nINIT 0F80\r\nPLAYER 3000\r\n"
    body = b"\xff\xff" + struct.pack("<HH", 0x2000, 0x1000) + b"\xea" * 8
    path = _w(tmp_path, "rev.sap", head + body)
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("before it starts" in w for w in warns), warns


def test_a_block_running_past_the_end_is_reported_not_raised(tmp_path):
    """The spec calls this malformed but notes players accept it, so it is a
    warning and the walk still returns what it read."""
    head = b"SAP\r\nAUTHOR \"x\"\r\nTYPE B\r\nINIT 0F80\r\nPLAYER 3000\r\n"
    body = b"\xff\xff" + struct.pack("<HH", 0x2000, 0x2FFF) + b"\xea" * 8
    path = _w(tmp_path, "cut.sap", head + body)
    chunks, warns = chiptune.inspect_sap(path)
    assert any("ends mid-block" in w for w in warns), warns
    assert len(chunks) == 2


def test_a_block_loading_into_hardware_registers_is_flagged(tmp_path):
    head = b"SAP\r\nAUTHOR \"x\"\r\nTYPE B\r\nINIT 0F80\r\nPLAYER 3000\r\n"
    body = b"\xff\xff" + struct.pack("<HH", 0xD200, 0xD20F) + b"\xea" * 16
    path = _w(tmp_path, "hw.sap", head + body)
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("hardware register" in w for w in warns), warns


def test_covox_only_accepts_d600(tmp_path):
    tags = ['AUTHOR "x"', "TYPE B", "INIT 0F80", "PLAYER 3000", "SONGS 1",
            "TIME 01:00", "COVOX D700"]
    path = _w(tmp_path, "cv.sap", _sap(tags=tags))
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("D600" in w for w in warns), warns


def test_bare_lf_line_endings_are_parsed_but_reported(tmp_path):
    """The spec asks for CR LF and says nothing about tolerance. Real files are
    unmeasured here, so the walker parses tolerantly and reports the deviation
    rather than refusing -- the forensic signal without the false negative."""
    blob = _sap(eol="\n")
    blob = b"SAP\r\n" + blob[blob.index(b"\n") + 1:]
    path = _w(tmp_path, "lf.sap", blob)
    chunks, warns = chiptune.inspect_sap(path)
    assert chunks[0]["fields"], "the header still parsed"
    assert any("CR LF" in w for w in warns), warns


def test_a_byte_outside_the_atascii_set_is_flagged(tmp_path):
    blob = _sap(tags=['AUTHOR "x"', "TYPE B", "INIT 0F80", "PLAYER 3000",
                      "NAME \"a~b\""])
    path = _w(tmp_path, "as.sap", blob)
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("ATASCII" in w for w in warns), warns
