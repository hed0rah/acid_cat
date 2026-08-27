"""PSID / RSID: the Commodore 64 SID tune file.

A .sid file is not audio. It is a header describing a 6502 machine-code music
player, followed by that player and its data as a raw C64 memory image. Nothing
in the file is a sample, a note, or a waveform -- playback means loading the
image into an emulated C64, calling an init routine with the song number in the
accumulator, and then calling a play routine at interrupt rate while the SID
chip's registers are written by the code itself. The "audio" is a side effect of
executing it.

That makes acidcat's job here structural rather than acoustic, and the header
carries a surprising amount: title, author, release, how many subtunes, which
SID chip the music was written for, which video standard it expects, where in
C64 memory it wants to sit, and whether a second or third SID is being addressed.

TWO MAGICS, ONE FORMAT. 'PSID' is the permissive original. 'RSID' declares that
the tune needs a real C64 environment and will not survive the shortcuts older
emulators took -- so RSID additionally REQUIRES loadAddress, playAddress and
speed to all be zero and the version to be 2 or higher. Those are not
recommendations: the spec says a reader must reject an RSID that breaks them.

THE HEADER IS BIG-ENDIAN. Every multi-byte header field is Motorola order, on a
file format describing a little-endian 6502. The format was designed on the
Amiga, and the byte order followed the host rather than the target. The C64 data
after the header is little-endian, so a single file is read in both directions
and the boundary is exactly at dataOffset. Getting this backwards is the first
mistake anyone makes here.

Layout and constraints verified against the HVSC SID_file_format.txt (authors
Schwendt, White, Lem, Bos), and every field below measured against 630 real
tunes from HVSC Update #85.
"""

import hashlib
import struct

MAGIC_PSID = b"PSID"
MAGIC_RSID = b"RSID"
MAGICS = (MAGIC_PSID, MAGIC_RSID)

# The header has exactly two sizes, and the version picks which. There is no
# third: v2, v3 and v4 all use 0x7C and differ only in which trailing bytes are
# meaningful.
HEADER_V1 = 0x76
HEADER_V2 = 0x7C

# Below this a C64 cannot safely load: the RSID floor. $07E8 is just past the
# default BASIC/screen workspace.
RSID_MIN_LOAD = 0x07E8

# The two ROM windows. An RSID init routine may not point into either, because
# the RSID environment leaves the bank register at 0x37 with both ROMs visible.
ROM_BASIC = (0xA000, 0xBFFF)
ROM_KERNAL_IO = (0xD000, 0xFFFF)

_CLOCK = {0: "unknown", 1: "PAL", 2: "NTSC", 3: "PAL and NTSC"}
_MODEL = {0: "unknown", 1: "MOS6581", 2: "MOS8580", 3: "MOS6581 and MOS8580"}


def clock_name(bits):
    return _CLOCK.get(bits & 3, "unknown")


def model_name(bits):
    return _MODEL.get(bits & 3, "unknown")


def decode_text(raw):
    """Decode a header string field.

    The spec says Windows-1252, not Latin-1 and not UTF-8. They agree
    everywhere except 0x80-0x9F, where cp1252 has typographic characters
    (curly quotes, dashes, the euro) and Latin-1 has C1 control codes. Five
    byte values are undefined in cp1252 and raise, so Latin-1 backstops them --
    it maps every possible byte and therefore cannot fail.

    Measured over 630 tunes: 81 string fields carry bytes above 0x7F, all of
    them accented Latin letters that both encodings agree on. The distinction
    is spec-driven rather than corpus-driven at this size, which is exactly
    when it is cheapest to get right.
    """
    raw = raw.split(b"\x00")[0]
    try:
        return raw.decode("cp1252").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def is_terminated(raw):
    """Whether a 32-byte string field has a NUL in it at all.

    The spec allows a full 32 characters with no terminator, which is the
    classic way to read a SID field wrong: a C-string read of `name` that does
    not stop at 32 runs straight into `author`. 37 of 1,890 fields across 630
    tunes are exactly this shape.
    """
    return b"\x00" in raw


def sid_address(value):
    """The absolute address of an extra SID chip, or None.

    The byte encodes the middle of $Dxx0: 0x42 means $D420. Only even values
    in 0x42-0x7F or 0xE0-0xFE are legal; everything else -- including 0x00 --
    means "no chip here". The two gaps are not arbitrary: 0x00-0x41 would
    collide with the first SID and the VIC-II, and 0x80-0xDF would land in
    colour RAM and the CIAs.
    """
    if value % 2:
        return None
    if 0x42 <= value <= 0x7F or 0xE0 <= value <= 0xFE:
        return 0xD000 | (value << 4)
    return None


def song_speed(speed, song, songs, psid_specific, version):
    """"VBI" or "CIA" for a 1-based song number.

    One bit per song, bit 0 being song 1. What happens past 32 songs depends on
    the header generation, and the two rules genuinely differ: v1, and v2+ with
    the PlaySID-specific flag set, WRAP -- song 33 reuses bit 0. v2NG and later
    with that flag clear instead REPEAT bit 31 for every song above 32.

    A tune with more than 32 subtunes is where those two disagree, and the
    header alone tells you which reading applies.
    """
    idx = song - 1
    if idx >= 32:
        if version == 1 or psid_specific:
            idx %= 32
        else:
            idx = 31
    return "CIA" if (speed >> idx) & 1 else "VBI"


def parse_header(raw):
    """Decode a SID header. Never raises: short input yields what is present.

    Returns a dict with every field, plus derived values: the effective load
    address (which may live in the C64 data rather than the header), the extent
    of the memory image, and the decoded flag bitfields.
    """
    # Every key is populated up front. An early return that fills in only some
    # of them makes the caller's `h["start_page"]` a KeyError on exactly the
    # inputs this function exists to survive -- and a parser that raises on a
    # short file is the failure it was written to prevent.
    h = dict(
        magic=bytes(raw[:4]), truncated=len(raw) < HEADER_V1,
        version=0, data_offset=0, load_address=0, init_address=0,
        play_address=0, songs=0, start_song=0, speed=0,
        name="", author="", released="",
        name_terminated=True, author_terminated=True, released_terminated=True,
        has_v2_fields=False, flags=0, start_page=0, page_length=0,
        second_sid_byte=0, third_sid_byte=0,
        mus_player=False, psid_specific=False, clock=0, sid_model=0,
        sid_model_2=0, sid_model_3=0, flags_reserved=0,
        second_sid=None, third_sid=None,
        load_in_data=True, effective_load=0, code_offset=0, code_length=0,
        memory_end=0, effective_init=0,
    )
    h["is_rsid"] = h["magic"] == MAGIC_RSID
    h["is_psid"] = h["magic"] == MAGIC_PSID

    if len(raw) < 0x16:
        # not even the fixed numeric block is present
        return h

    (h["version"], h["data_offset"], h["load_address"], h["init_address"],
     h["play_address"], h["songs"], h["start_song"]) = struct.unpack_from(
        ">HHHHHHH", raw, 4)
    h["speed"], = struct.unpack_from(">I", raw, 0x12)

    for name, off in (("name", 0x16), ("author", 0x36), ("released", 0x56)):
        field = raw[off:off + 32]
        h[name] = decode_text(field)
        h[name + "_terminated"] = is_terminated(field) if len(field) == 32 else True

    # v2+ tail. Present only if the file actually holds it, which a truncated
    # file will not -- and reporting a flags word read off the end of a short
    # file as though it were declared is how a parser invents a fact.
    h["has_v2_fields"] = h["version"] >= 2 and len(raw) >= HEADER_V2
    if h["has_v2_fields"]:
        h["flags"], = struct.unpack_from(">H", raw, 0x76)
        h["start_page"] = raw[0x78]
        h["page_length"] = raw[0x79]
        h["second_sid_byte"] = raw[0x7A]
        h["third_sid_byte"] = raw[0x7B]
    else:
        h["flags"] = 0
        h["start_page"] = h["page_length"] = 0
        h["second_sid_byte"] = h["third_sid_byte"] = 0

    f = h["flags"]
    h["mus_player"] = bool(f & 1)
    h["psid_specific"] = bool((f >> 1) & 1)
    h["clock"] = (f >> 2) & 3
    h["sid_model"] = (f >> 4) & 3
    h["sid_model_2"] = (f >> 6) & 3
    h["sid_model_3"] = (f >> 8) & 3
    h["flags_reserved"] = f >> 10
    h["second_sid"] = sid_address(h["second_sid_byte"])
    h["third_sid"] = sid_address(h["third_sid_byte"])

    # The load address may not be in the header at all. Zero means the first
    # two bytes of the C64 data hold it, little-endian, the way any C64 binary
    # written by SAVE does. All 630 measured tunes take this route, so the
    # "embedded" path is the normal one and the header field is the exception.
    off = h["data_offset"]
    h["load_in_data"] = h["load_address"] == 0
    if h["load_in_data"] and len(raw) >= off + 2:
        h["effective_load"], = struct.unpack_from("<H", raw, off)
        h["code_offset"] = off + 2
    else:
        h["effective_load"] = h["load_address"]
        h["code_offset"] = off

    h["code_length"] = max(0, len(raw) - h["code_offset"])
    h["memory_end"] = (h["effective_load"] + h["code_length"] - 1
                       if h["code_length"] else h["effective_load"])
    # An init of 0 means "same as the load address", which is a real answer and
    # not a missing one.
    h["effective_init"] = h["init_address"] or h["effective_load"]
    return h


def header_size(version):
    return HEADER_V1 if version == 1 else HEADER_V2


def in_rom(addr):
    return (ROM_BASIC[0] <= addr <= ROM_BASIC[1]
            or ROM_KERNAL_IO[0] <= addr <= ROM_KERNAL_IO[1])


def violations(h, filesize):
    """Spec violations, as (field, complaint) pairs.

    Everything here is a MUST in the format description, not a preference. The
    RSID rules are the strict ones: the spec says a reader must reject an RSID
    that breaks them, because those fields are what force a tune to configure
    real hardware instead of relying on an emulator's shortcuts.
    """
    out = []
    v = h.get("version", 0)
    if h["magic"] not in MAGICS:
        out.append(("magicID", "not 'PSID' or 'RSID'"))
    if v not in (1, 2, 3, 4):
        out.append(("version", "%d is outside the defined range 1-4" % v))
    expected = header_size(v)
    if v in (1, 2, 3, 4) and h["data_offset"] != expected:
        out.append(("dataOffset",
                    "0x%04X for a v%d header, expected 0x%04X"
                    % (h["data_offset"], v, expected)))
    if not 1 <= h["songs"] <= 0x100:
        out.append(("songs", "%d is outside 1-256" % h["songs"]))
    if h["songs"] and not 1 <= h["start_song"] <= h["songs"]:
        out.append(("startSong",
                    "%d is not within 1-%d" % (h["start_song"], h["songs"])))
    if h["data_offset"] > filesize:
        out.append(("dataOffset", "points past the end of the file"))
    if h.get("flags_reserved"):
        out.append(("flags", "bits 10-15 are reserved and should be 0"))

    if h["is_rsid"]:
        if v < 2:
            out.append(("version", "RSID requires version 2 or higher"))
        if h["load_address"]:
            out.append(("loadAddress",
                        "RSID requires 0, found $%04X" % h["load_address"]))
        if h["play_address"]:
            out.append(("playAddress",
                        "RSID requires 0, found $%04X" % h["play_address"]))
        if h["speed"]:
            out.append(("speed",
                        "RSID requires 0, found 0x%08X" % h["speed"]))
        if h["effective_load"] and h["effective_load"] < RSID_MIN_LOAD:
            out.append(("loadAddress",
                        "RSID load must not be below $%04X, found $%04X"
                        % (RSID_MIN_LOAD, h["effective_load"])))
        init = h["init_address"]
        if init and in_rom(init):
            out.append(("initAddress",
                        "RSID init must not point into ROM, found $%04X" % init))
        if h["psid_specific"] and init:
            out.append(("initAddress",
                        "the C64 BASIC flag is set, so init must be 0"))
    # startPage/pageLength describe a free region for a relocated driver; the
    # spec pins pageLength to 0 at both sentinel values of startPage.
    if h["start_page"] in (0x00, 0xFF) and h["page_length"]:
        out.append(("pageLength",
                    "must be 0 when startPage is 0x%02X" % h["start_page"]))
    if h["third_sid_byte"] and h["third_sid_byte"] == h["second_sid_byte"]:
        out.append(("thirdSIDAddress",
                    "cannot be the same address as the second SID"))
    return out


def songlength_md5(raw):
    """The HVSC song-length database key.

    Since HVSC #71 this is simply MD5 over the entire file, header included,
    and it is how a player looks a tune up in Songlengths.md5 to learn how long
    each subtune runs. Verified against all 630 tunes in HVSC Update #85
    against that file's 61,157 entries: 630 matched.

    Before #71 the key was an MD5 over a synthetic byte stream -- the C64 data,
    then init, play and song count as little-endian words, then one byte per
    speed bit (0 for VBI, 60 for CIA), then a trailing 2 for NTSC. That old
    form is not computed here: the database that would verify it has not
    shipped since #71, and a hash nothing can check is a number, not a fact.
    """
    return hashlib.md5(raw).hexdigest()


def looks_like_sid(head):
    """Cheap identification for sniff.

    The magic is four bytes and the version immediately qualifies it, which is
    enough to be safe: 'PSID'/'RSID' followed by a version of 1-4 is not a
    pattern that occurs by accident.
    """
    if len(head) < 8 or head[:4] not in MAGICS:
        return False
    version, = struct.unpack_from(">H", head, 4)
    return 1 <= version <= 4
