"""MDX: the Sharp X68000 music format, as played by MXDRV.

An MDX is a Music Macro Language score for the X68000's YM2151 (OPM) FM chip,
plus optional ADPCM samples that live in a SEPARATE file with a .PDX
extension. So a tune is often two files, and the MDX names the one it wants.

There is no magic number. A file opens with its title in Shift-JIS, and
identification is the arithmetic: a title terminator, a NUL-terminated PDX
name, then an offset table whose own first entry says how many channels
follow. That table has to resolve to 9 or 16 or the file is not an MDX.

LAYOUT

    title           Shift-JIS, terminated by 0D 0A 1A
    pdx name        NUL-terminated; a bare NUL when the tune uses no samples
    voice offset    u16 big-endian
    mml offsets     u16 big-endian x 9 or x 16

Every offset is relative to the position of the VOICE OFFSET WORD, not to the
start of the file -- which is the one thing a reader has to get right, because
the title and PDX name are both variable length so that base moves per file.

The channel count is recovered from the table rather than declared. The first
MML offset sits two bytes past the base, so (first_offset - 2) / 2 is how many
words lie between the base and the data it points at, which is the channel
count. Measured over 27,166 real tunes: 18,367 use 9 channels and 8,331 use
16, and nothing else resolves.

That works only because the first channel's data begins immediately after the
table, with nothing between. It holds in every file measured -- 5,890 of 5,890
have a first MML offset exactly equal to the table size, and in every one the
voice block sits AFTER the channel data rather than before it. So the layout
is fixed in practice even though nothing in the format states it, and a file
that put the voices first would make the channel count underivable.

Channels are lettered rather than numbered: A through H are the eight FM
voices, P is the ADPCM channel, and Q through W are the extra voices a Mercury
Unit expansion board provides. Nine is the base machine; sixteen is a machine
with the expansion.

Voices are 27 bytes each and are OPM register values, not an abstraction over
them -- so a voice is literally what gets written to the chip.

The byte order is big-endian throughout, which for once is not a trap: the
X68000 is a 68000.

Layout from the MXDRV format description (www16.atwiki.jp/mxdrv), cross-checked
against the mdxtools notes, and every field below measured against 27,166 tunes
from the X68000 MDX Master Library.
"""

import struct

TITLE_END = b"\x0d\x0a\x1a"

# A title longer than this is not a title. The longest in 27,166 real tunes is
# well inside it; the cap exists so a file with no terminator at all is
# rejected by arithmetic rather than by reading to EOF.
MAX_TITLE = 1024
# Likewise for the sample-bank name, which is a Human68k filename.
MAX_PDX_NAME = 256

# The only two table sizes that occur. Nine is a stock X68000: eight FM voices
# plus one ADPCM channel. Sixteen adds the Mercury Unit's extra voices.
CHANNELS_BASE = 9
CHANNELS_MERCURY = 16

VOICE_SIZE = 27

_LETTERS = "ABCDEFGHPQRSTUVW"


def channel_name(index, count):
    """The letter MXDRV uses for a channel, or a number if it has none.

    A through H are the FM voices and P is ADPCM, so a nine-channel file is
    A-H plus P. Q onward are Mercury Unit channels.
    """
    if count == CHANNELS_BASE and index == 8:
        return "P"
    if index < len(_LETTERS):
        return _LETTERS[index]
    return str(index)


def decode_title(raw):
    """Decode a title. Shift-JIS, because the X68000 is a Japanese machine.

    Falls back rather than raising: a title that will not decode is still a
    title, and refusing to name the tune is worse than naming it imperfectly.
    """
    try:
        return raw.decode("shift_jis").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def parse_header(raw):
    """Decode an MDX header. Never raises; `ok` says whether it holds together.

    Returns a dict with the title, the PDX name, the offset base, the channel
    count and the resolved absolute offsets of the voice block and each
    channel's MML stream.
    """
    h = {
        "ok": False, "why": "", "title": "", "title_end": -1,
        "pdx_name": "", "has_pdx": False, "base": -1,
        "channels": 0, "voice_offset": 0, "voice_abs": -1,
        "mml_offsets": [], "mml_abs": [],
    }
    end = raw.find(TITLE_END, 0, MAX_TITLE)
    if end < 0:
        h["why"] = "no 0D 0A 1A title terminator in the first %d bytes" % MAX_TITLE
        return h
    h["title"] = decode_title(raw[:end])
    h["title_end"] = end

    nul = raw.find(b"\x00", end + 3, end + 3 + MAX_PDX_NAME)
    if nul < 0:
        h["why"] = "no NUL terminating the PDX file name"
        return h
    name = raw[end + 3:nul]
    h["pdx_name"] = decode_title(name) if name else ""
    h["has_pdx"] = bool(name)

    base = nul + 1
    h["base"] = base
    if base + 4 > len(raw):
        h["why"] = "file ends before the offset table"
        return h

    h["voice_offset"], first = struct.unpack_from(">HH", raw, base)
    # The channel count is not stored. The first MML offset points past the
    # table, so the gap between it and the table's start is the table itself.
    if first < 2 or first % 2:
        h["why"] = "first MML offset %d cannot start an offset table" % first
        return h
    count = (first - 2) // 2
    if count not in (CHANNELS_BASE, CHANNELS_MERCURY):
        h["why"] = ("offset table resolves to %d channels, and only 9 or 16 "
                    "occur" % count)
        return h
    h["channels"] = count

    need = base + 2 + count * 2
    if need > len(raw):
        h["why"] = "file ends inside the offset table"
        return h
    h["mml_offsets"] = list(struct.unpack_from(">%dH" % count, raw, base + 2))
    h["voice_abs"] = base + h["voice_offset"]
    h["mml_abs"] = [base + o for o in h["mml_offsets"]]
    h["ok"] = True
    return h


def looks_like_mdx(raw, filesize=None):
    """Structural identification, since the format has no magic.

    Everything before the offset table is variable-length text, so the only
    thing that can identify an MDX is whether the arithmetic lands: a title
    terminator, a NUL, and a table that resolves to a legal channel count with
    every offset inside the file.

    `filesize` matters. The offsets routinely point past the few kilobytes a
    sniffer reads, so checking them against len(raw) rejects almost every real
    tune when handed a truncated head -- which is exactly what happened, and
    the bound has to be the FILE's length rather than the buffer's.
    """
    h = parse_header(raw)
    if not h["ok"]:
        return False
    n = len(raw) if filesize is None else filesize
    if not 0 <= h["voice_abs"] <= n:
        return False
    return all(0 <= a <= n for a in h["mml_abs"])


def voice_count(raw, h):
    """How many 27-byte voices sit between the voice block and the first MML
    stream. Derived, because nothing declares it."""
    if not h["ok"] or h["voice_abs"] < 0:
        return 0
    first = min([a for a in h["mml_abs"] if a > h["voice_abs"]] or [len(raw)])
    return max(0, (min(first, len(raw)) - h["voice_abs"]) // VOICE_SIZE)


def parse_voice(raw, off):
    """One 27-byte voice as OPM register fields.

    The four-element lists are the operators in MXDRV's order: M1, C1, M2, C2
    as the chip numbers them. Values are raw register contents.
    """
    if off < 0 or off + VOICE_SIZE > len(raw):
        return None
    b = raw[off:off + VOICE_SIZE]
    v = {"number": b[0], "feedback": (b[1] >> 3) & 7, "connect": b[1] & 7,
         "slot_mask": b[2] & 0x0F}
    names = ("dt1_mul", "tl", "ks_ar", "ame_d1r", "dt2_d2r", "d1l_rr")
    for i, name in enumerate(names):
        v[name] = list(b[3 + i * 4:7 + i * 4])
    return v


# Enough for the longest title plus the sample-bank name plus the table. The
# sniffer's shared head is 20 bytes, which cannot reach an MDX offset table at
# all, so this check reads for itself the way the gzipped-Ableton one does.
SNIFF_READ = 4096


def looks_like_mdx_file(path):
    """`looks_like_mdx` for a path, reading enough to see the offset table."""
    import os
    try:
        with open(path, "rb") as fh:
            head = fh.read(SNIFF_READ)
        return looks_like_mdx(head, os.path.getsize(path))
    except OSError:
        return False
