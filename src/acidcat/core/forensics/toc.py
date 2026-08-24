"""Find a table of contents in a container nobody has written a walker for.

acidcat supports audio formats, major and obscure. It deliberately does not
try to know what every *container* is -- what a Wii disc or a game mod archive
happens to be is not its business, and a walker per proprietary archive is a
losing game. What it does need is the ability to open one anyway and find the
audio inside.

A signature sweep can do that, and does. But a huge family of archives carries
something far better than a signature: an index. Two shapes account for most of
them, and both recur because each is the obvious way to write one in its era.

**Length-prefixed** (`find_toc`), the shape a modern serializer emits --

    <length> <name bytes> <fixed-width integer fields> <length> <name> ...

**Fixed-width** (`find_fixed_toc`), the shape a C `struct` array emits --

    <name padded to W bytes> <integer fields>   repeated every S bytes

The second is not a variant of the first, and reading one does not read the
other. Measured across six shipped game archives, the fixed-width family also
moves the name around inside the record: Quake and Duke Nukem put it first,
Half-Life and Doom put it last, behind the integers. So the stride, the width,
and where the name sits within the record are all discovered rather than
assumed, which is what makes one detector cover all of them.

Neither knows any format. The evidence is a layout that keeps predicting where
the next entry begins, and a wrong layout stops within an entry or two. Both
report a hypothesis in the voice `--force` uses -- candidate entries with the
layout that produced them, never an identification.

`read_toc` is the whole job for a caller holding a file: pick the windows, run
both detectors, and settle the remaining ambiguities by verifying that the
integer fields actually land payloads on their magic bytes.
"""

import collections
import os
import re
import struct

# A name has to look like one, and "contains a dot or a slash" is not enough.
#
# Measured on freedoom1.wad: this returned a 100-entry table at confidence 0.84
# whose names were the CONCRETE wall texture. Eight-bit image data sits in a
# narrow band of byte values, that band overlaps printable ASCII, and 0x5C is an
# ordinary pixel -- so a run of graphics satisfied "looks like a Windows path".
# The same is true of 8-bit audio. A detector aimed at finding audio inside
# unknown containers was being fooled by the audio.
#
# So the test is what actually separates a filename from smooth bytes that
# happen to be printable:
#
#   mostly alphanumeric   `Sounds/Music/Lure.ogg` is ~86% [A-Za-z0-9]; the
#                         texture was 39%, the rest being []^_` -- punctuation
#                         that is contiguous with the letters in ASCII and so
#                         appears wherever a smooth ramp crosses that range
#   a structured separator   a dot needs a short alphanumeric extension after
#                         it, a slash needs name characters on both sides. In
#                         the texture the backslashes sat between [ and ], which
#                         is not a path, it is a coincidence
#
# Both are cheap, and each one alone rejects the specimen that motivated them.
#
# KNOWN COST, measured rather than assumed. The alphanumeric floor rejects
# real names that are mostly punctuation: `__VER__`, a version stub at the
# head of several id WADs, is 3 alphanumerics in 7 and scores 0.43. Counting
# underscore as a name character would recover it and was tried -- it takes
# the wall-texture specimen from 4 printable runs over the floor to 12 of 17,
# because the band that texture crawls through is exactly []^_` and the
# underscore is in it. One lump of 1,674 is the cheaper loss, and the lumps
# lost this way are version stubs rather than audio.
_NAME = re.compile(rb"[\x20-\x7e]{3,255}")
_SEPARATOR = re.compile(rb"[./\\]")
_EXTENSION = re.compile(rb"\.[A-Za-z0-9]{1,5}$")
_PATH_SEP = re.compile(rb"[A-Za-z0-9_)\]-][/\\][A-Za-z0-9_(\[-]")
_ALNUM_FLOOR = 0.6

# A filename draws on several ASCII classes -- a dot at 0x2E, digits at 0x30,
# capitals at 0x41, lowercase at 0x61 -- so its byte values are spread wide.
# `CalamityModMusic.dll` spans 75. Smooth 8-bit data crosses printable ASCII as
# a narrow contiguous ramp: the second false table freedoom produced spanned
# NINE values, [ through d, and every "name" in it was letters and the
# punctuation that happens to sit between them in the ASCII table. Alphanumeric
# ratio cannot see this, because a, b, c and d are alphanumeric.
_MIN_BYTE_SPAN = 32


def _alnum_ratio(name):
    if not name:
        return 0.0
    n = sum(1 for c in name if (48 <= c <= 57) or (65 <= c <= 90)
            or (97 <= c <= 122))
    return n / float(len(name))


def _looks_like_a_name(name):
    """Does this run of printable bytes plausibly name something?

    Deliberately strict. A missed table is a container we cannot read yet; a
    false table is a confident wrong answer with a filename attached to it, and
    this function exists because the second one shipped.
    """
    if not _SEPARATOR.search(name):
        return False
    if max(name) - min(name) < _MIN_BYTE_SPAN:
        return False
    if _alnum_ratio(name) < _ALNUM_FLOOR:
        return False
    # the separator has to be doing a separator's job
    return bool(_EXTENSION.search(name) or _PATH_SEP.search(name))

# No internal read cap. The caller already chooses how much to hand over --
# the TUI passes the first 2 MB -- and a second, hidden truncation inside would
# mean a table just past it went missing with nobody in a position to say so.
# Whoever picks the window owns the consequence of picking it.
# Measured, not guessed: over 137 ordinary audio files (wav/mp3/flac/ogg/aiff/
# midi and several preset formats) the longest chance chain was 7 entries, and
# only five files produced one at all. Twelve is comfortably clear of that while
# a real table runs to hundreds -- the tModLoader archive this was built against
# has 266.
_MIN_ENTRIES = 12
_MAX_FIELDS = 4                  # int32 fields between a name and the next


def _varint_before(data, at):
    """(value, start) for a 7-bit varint ending just before `at`, or None."""
    for width in (1, 2, 3):
        start = at - width
        if start < 0:
            return None
        run = data[start:at]
        if all(b & 0x80 for b in run[:-1]) and not (run[-1] & 0x80):
            value = 0
            for i, b in enumerate(run):
                value |= (b & 0x7F) << (7 * i)
            return value, start
    return None


def _prefix_at(data, at, kind):
    """(declared_length, entry_start) for the length prefix before `at`."""
    if kind == "varint":
        return _varint_before(data, at)
    width, fmt = {"u8": (1, "B"), "u16le": (2, "<H"), "u16be": (2, ">H"),
                  "u32le": (4, "<I"), "u32be": (4, ">I")}[kind]
    start = at - width
    if start < 0 or at > len(data):
        return None
    return struct.unpack_from(fmt, data, start)[0], start


_PREFIXES = ("varint", "u8", "u16le", "u16be", "u32le", "u32be")


def _chain_from(data, name_at, kind, nfields, field_width=4):
    """Entries found by repeatedly applying one layout from `name_at`."""
    out = []
    n = len(data)
    at = name_at
    while len(out) < 100000:
        got = _prefix_at(data, at, kind)
        if got is None:
            break
        length, start = got
        if not (3 <= length <= 255) or at + length > n:
            break
        name = data[at:at + length]
        if not _NAME.fullmatch(name):
            break
        after = at + length
        end = after + nfields * field_width
        if end > n:
            break
        raw = data[after:end]
        # If every byte of the "fields" is printable, they are text being read
        # as integers -- the other half of how prose chained.
        if all(0x20 <= b <= 0x7E for b in raw):
            break
        fields = [struct.unpack_from("<i", data, after + i * field_width)[0]
                  for i in range(nfields)]
        if any(f < 0 for f in fields):
            break
        out.append({"offset": start, "name": name.decode("utf-8", "replace"),
                    "fields": fields, "name_at": at, "end": end})
        # the next entry begins with its own prefix at `end`
        step = _prefix_width(data, end, kind)
        if step is None:
            break
        at = end + step
    return out


def _prefix_width(data, at, kind):
    """How many bytes the prefix starting at `at` occupies, or None."""
    if kind != "varint":
        return {"u8": 1, "u16le": 2, "u16be": 2, "u32le": 4, "u32be": 4}[kind]
    for width in (1, 2, 3):
        if at + width > len(data):
            return None
        if not (data[at + width - 1] & 0x80):
            return width
    return None


def _confidence(entries):
    """How much of a table this is, from length AND from the names.

    Length alone said 0.84 about a wall texture: a long chain only proves the
    layout is self-consistent, and smooth bytes are extremely self-consistent.
    Two independent things have to hold.

    Capped below a signature match on purpose: this is a shape, not a magic
    number, and it must never outrank something that verified one.
    """
    n = len(entries)
    names = [e["name"].encode("latin-1", "replace")
             if isinstance(e["name"], str) else e["name"] for e in entries]
    quality = sum(_alnum_ratio(nm) for nm in names) / float(len(names))
    # A table of real paths shares structure between siblings -- an extension
    # set, a directory prefix. One that does not is a weaker hypothesis even if
    # every entry passed on its own.
    exts = {nm.rsplit(b".", 1)[-1].lower() for nm in names if b"." in nm}
    shared = len(exts) <= max(3, len(names) // 8)
    length_term = min(0.85, 0.4 + n / 200.0)
    conf = length_term * quality * (1.0 if shared else 0.75)
    return round(min(0.85, conf), 2)


def find_toc(data, min_entries=_MIN_ENTRIES):
    """The best length-prefixed table-of-contents hypothesis, or None.

    Returns {"entries": [...], "prefix": kind, "fields": n, "offset": start,
    "confidence": float}. `entries` carry a name and the integer fields that
    followed it, which for every archive of this shape are its size and offset.
    """
    best = None
    seen_starts = set()
    for m in _NAME.finditer(data):
        name_at = m.start()
        if name_at in seen_starts or not _looks_like_a_name(m.group(0)):
            continue
        for kind in _PREFIXES:
            for nfields in range(1, _MAX_FIELDS + 1):
                entries = _chain_from(data, name_at, kind, nfields)
                if len(entries) < min_entries:
                    continue
                if best is None or len(entries) > len(best["entries"]):
                    best = {"entries": entries, "prefix": kind,
                            "fields": nfields, "offset": entries[0]["offset"]}
        if best and len(best["entries"]) > 50:
            break                      # a chain this long is the answer
        seen_starts.add(name_at)
    if best is None:
        return None
    best["shape"] = "length-prefixed"
    best["confidence"] = _confidence(best["entries"])
    return best


# ---------------------------------------------------------------------------
# The other shape: an array of fixed-width records
# ---------------------------------------------------------------------------
#
# Everything above reads a serializer's output, where the name carries its own
# length. An archive written as a C `struct` array does not: the name sits in a
# fixed-width character field, padded with NULs, and the reader knows the width
# because it is compiled in. Nothing marks where a record starts.
#
# What does mark it is the stride. A run of hundreds of name-like fields at one
# exact spacing is not something a chance arrangement of bytes produces, and it
# is measurable without knowing anything about the format. Verified against the
# shipped archives of six games:
#
#   Quake / Quake II / Hexen II  .pak    stride  64, name at +0  width 56
#   Duke Nukem 3D / Shadow Warrior .grp  stride  16, name at +0  width 12
#   Half-Life                    .wad3   stride  32, name at +16 width 16
#   Doom                         .wad    stride  16, name at +8  width  8
#
# The last two put the name behind the integers, so which side of the name the
# fields sit on is a question this has to answer rather than assume.

# The run is matched without the terminator and the NUL is checked separately,
# which is the same thing and not the same cost. As one pattern,
# `[\x20-\x7e]{3,}\x00` makes the engine match a printable run greedily and then
# give bytes back one at a time looking for a NUL that is not there, at every
# starting position -- quadratic in the length of the run. Eight-bit audio is
# one long printable run, so the 8 MB tail of SW.GRP took 24.6 seconds to yield
# 287 anchors. Split, the same scan is linear and finishes in well under one.
_RUN_ANY = re.compile(rb"[\x20-\x7e]{3,}")
_MIN_STRIDE = 8
_MAX_STRIDE = 512
_STRIDE_LOOKAHEAD = 6            # anchors ahead to draw a candidate stride from
_MIN_NAME = 3
_MAX_RECORDS = 200000
_WALK_GRACE = 24                 # records before the padding ratio is judged
# A resource decision, and one that costs accuracy: in four Doom WADs the real
# directory ranks 178th, 246th, 318th and 36th among the chains their tail
# window contains, so three of them are never validated at all. Raising the cap
# to 200, 400 and 800 was measured over 32 shipped archives and recovers ONE of
# them for half again the wall time; past 200 nothing further changes. Left at
# 80 because a 5x cost for one archive is not a trade worth making blind, and
# because ranking, not budget, is the thing actually getting it wrong.
_MAX_CANDIDATE_CHAINS = 80
_OUT_OF_ORDER = 0.05             # of an offset column's steps, may descend
_WIDTH_SUPPORT = 0.1             # of records that must agree on a field width
_WIDTHS_TRIED = 3                # candidate widths judged per stride chain
# Records at each edge the payloads may reclaim, no-trim first so that a table
# needing no correction is never second-guessed into one.
_EDGE_TRIMS = ((0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (0, 2),
               (2, 1), (1, 2), (2, 2))
_NO_OVERLAP = 0.95               # of payload extents that must not collide
_UNCLOSED = 0.6                  # weight when the last payload misses the index
_CLOSE_SLACK = 16                # padding allowed behind the last payload
_MAX_CHECKS = 64                 # payload magics read per placement hypothesis

# A real index names distinct things. Smooth data chained at a constant stride
# produces the same few near-identical runs over and over: the false tables this
# rejected during development scored 0.01 to 0.22 here while every genuine
# directory scored 0.96 or better.
_UNIQ_FLOOR = 0.5
# The sharper half of the same question, and the one a ratio of distinct names
# cannot ask. Reading a real index at half its stride produces a second chain
# that is perfectly self-consistent and twice as long: every other record is the
# real one, and the records between them are an integer field mistaken for a
# name, so they are all THE SAME STRING. Quake's pak2 read at 32 rather than 64
# yields 116 entries of which 58 are the literal " AB", and distinctness lands
# at 0.509 -- over the floor above by nine thousandths, which is not a margin,
# it is a coincidence. How much of a table ONE repeated name accounts for
# separates the two cleanly. Measured over 1,644 candidate readings of 32
# shipped archives: the worst genuine index is Doom's, at 0.0769, because
# THINGS appears once per map; every false reading of a real directory sits
# between 0.38 and 0.52. This sits 3x above the one and 2x below the others.
_REPEAT_CEIL = 0.25
# NUL padding is the half of the evidence that smooth data cannot fake -- 0x00
# is nowhere near the printable band that made a wall texture look like a list
# of paths. It cannot be required of every record, because a quarter of the
# names in DUKE3D.GRP fill their twelve-byte field exactly and so carry none.
_PAD_FLOOR = 0.2


def _anchors(data):
    """Positions of printable runs that end at a NUL terminator.

    The strong signal. NUL padding is what smooth data cannot fake: 0x00 is
    nowhere near the printable band that once made a wall texture look like a
    list of paths, so a run that ends in one is evidence of a character field
    rather than of bytes that happen to be readable.
    """
    n = len(data)
    return [m.start() for m in _RUN_ANY.finditer(data)
            if m.end() < n and data[m.end()] == 0]


def _starts_a_name(data, p):
    """The weak test: printable bytes at exactly this position.

    Used to EXTEND a chain, never to propose one. A stride is only proposed
    from NUL-terminated names, which are rare outside a table; but demanding
    that of every record would truncate a Duke Nukem directory at its first
    twelve-character filename, and there are 123 of those in 456 entries.
    """
    if p < 0 or p + _MIN_NAME > len(data):
        return False
    for i in range(_MIN_NAME):
        if not (0x20 <= data[p + i] <= 0x7E):
            return False
    return True


def _padded_width(data, p, cap):
    """Width of "printable run then NUL padding" at p, capped."""
    m = _NAME.match(data, p)
    if not m:
        return 0
    w = m.end() - p
    if w >= cap:
        return cap
    q = m.end()
    while w < cap and q < len(data) and data[q] == 0:
        q += 1
        w += 1
    return w


def _fixed_name(data, p, width):
    """The name in the fixed-width field at p, or None if it is not one.

    A printable run that either fills the field or ends at a NUL. What follows
    that NUL is deliberately not examined, because the format does not define
    it and real archives do not zero it: entry 266 of Quake's shipped PAK0.PAK
    reads

        progs.dat\\x00A\\x00\\x86\\x00\\x00\\x00\\x10E\\xfaw...

    -- a nine-byte name written into a 56-byte struct that still held whatever
    was on the stack, shipped on the CD that way in 1996 and still in the copy
    Steam sells. Every real reader takes the bytes up to the first NUL and
    ignores the rest, so requiring clean padding is a stricter rule than the
    format has, and it threw away a 339-entry directory over one record.
    """
    fld = data[p:p + width]
    if len(fld) < width:
        return None
    m = _NAME.match(fld)
    if not m:
        return None
    nm = m.group(0)
    if len(nm) < width and fld[len(nm)] != 0:
        return None                  # neither printable nor a terminator
    if _alnum_ratio(nm) < _ALNUM_FLOOR:
        return None
    return nm


def _walk(data, p, step, at, limit):
    """Follow the grid from p while it keeps looking like a table.

    The weak test alone would run off the end of a directory and straight
    through the payloads behind it: eight-bit image data has three printable
    bytes at almost every position, so an unbounded walk through a texture
    never stops. What actually runs out at the end of a table is NUL padding.

    That has to be measured as a proportion rather than as a gap, because a run
    of names that all fill their field exactly is normal and can be long:
    freedoom1.wad's directory is 64.5% NUL-terminated overall and still contains
    159 consecutive eight-character lump names. Any fixed gap short enough to
    stop a walk through a texture cuts that directory in half, so the test is
    instead whether padding has held up across the walk so far, after a short
    grace period for the first few records.

    What comes back is the furthest record that looked like one, not the
    furthest that was NUL-terminated. Those differ: entry 0 of Quake II's
    directory is a perfectly good name whose printable run begins six bytes
    early, inside the payload in front of it, so the run does not start on the
    grid and the record is not an anchor. Trimming the ends is `_longest_valid`'s
    job, and it can only do it for records this hands it.
    """
    last = p
    q = p
    steps = 0
    anchors = 1 if p in at else 0
    while steps < limit:
        q += step
        if not _starts_a_name(data, q):
            break
        steps += 1
        if q in at:
            anchors += 1
        elif steps > _WALK_GRACE and anchors < steps * _PAD_FLOOR:
            break
        last = q
    return last


def _stride_chains(data, min_entries):
    """Runs of name-like fields at one constant stride.

    Four consecutive NUL-terminated names propose a stride; the chain is then
    extended in both directions on the weak test. Proposing needs the strong
    signal because the search is over every position in the file, and extending
    needs the weak one because a real directory is allowed to contain a name
    that fills its field -- 123 of the 456 in DUKE3D.GRP do.
    """
    anchors = _anchors(data)
    at = set(anchors)
    seen = set()
    out = []
    n = len(anchors)
    for i, p in enumerate(anchors):
        for j in range(i + 1, min(i + 1 + _STRIDE_LOOKAHEAD, n)):
            s = anchors[j] - p
            if s > _MAX_STRIDE:
                break
            if s < _MIN_STRIDE or (p, s) in seen:
                continue
            if (p + 2 * s) not in at or (p + 3 * s) not in at:
                continue
            lo = _walk(data, p, -s, at, _MAX_RECORDS)
            hi = _walk(data, p, s, at, _MAX_RECORDS)
            chain = list(range(lo, hi + 1, s))
            for c in chain:
                seen.add((c, s))
            if len(chain) >= min_entries:
                out.append((chain, s, sum(1 for c in chain if c in at)))
    # Validating a chain costs a regex match per record, so only the most
    # promising are validated. Promise is measured in NUL-terminated names, not
    # in length: ordering by length dropped DUKE3D.GRP's real 459-record
    # directory below eighty longer chains of texture data, which is the same
    # mistake -- a count reported as a quality -- that this module exists to
    # avoid making about its own output.
    out.sort(key=lambda c: (c[2], c[2] / float(len(c[0]))), reverse=True)
    return [(chain, s) for chain, s, _n in out[:_MAX_CANDIDATE_CHAINS]]


def _longest_valid(data, chain, width, min_entries):
    """The longest unbroken run of records that read as names of this width.

    A chain extended on three printable bytes can pick up junk at either end --
    the file data just before a directory, the first payload just after it. The
    table is the part that survives the full test, and taking the longest run
    rather than rejecting the whole candidate is what keeps a good directory
    with one odd neighbour.
    """
    names = [_fixed_name(data, p, width) for p in chain]
    best_i, best_n, i = 0, 0, 0
    while i < len(names):
        if names[i] is None:
            i += 1
            continue
        j = i
        while j < len(names) and names[j] is not None:
            j += 1
        if j - i > best_n:
            best_i, best_n = i, j - i
        i = j
    if best_n < min_entries:
        return None
    return best_i, names[best_i:best_i + best_n]


def _fields_at(data, positions, start_off, nfields):
    """`nfields` little-endian int32s at start_off from each position."""
    out = []
    for p in positions:
        base = p + start_off
        if base < 0 or base + 4 * nfields > len(data):
            return None
        out.append([struct.unpack_from("<i", data, base + 4 * k)[0]
                    for k in range(nfields)])
    return out


def _offset_like(col, limit):
    """Does this column read as a series of file offsets?

    Mostly ascending, inside the file, and actually going somewhere. This is
    what tells a `filepos` column from a `size` column without reading a byte
    of payload, and it is the only evidence available in an archive whose
    entries carry no file extension to check a magic against.

    "Mostly", because nothing obliges an index to be sorted. Demanding that
    every single step ascend made one out-of-order entry in a 339-entry Quake
    directory disqualify the whole column, and the table lost to a reading of
    the same bytes that covered every other record.
    """
    n = len(col)
    if n < 2 or col[0] < 0:
        return False
    if not (0 <= min(col) and max(col) < limit):
        return False
    rising = falling = 0
    for a, b in zip(col, col[1:]):
        if b > a:
            rising += 1
        elif b < a:
            falling += 1
    steps = n - 1
    return (falling <= steps * _OUT_OF_ORDER
            and rising >= steps * 0.5
            and (max(col) - min(col)) > n)


def _pairing_score(fields, limit):
    """How well some pair of columns reads as (offset, size).

    `offset[k] + size[k] == offset[k+1]` is the arithmetic an archive writer
    performs while laying payloads down, so finding it is direct evidence that
    these integers are that archive's index -- and it settles which side of the
    name the fields belong to, which nothing about the bytes alone can.
    """
    if not fields:
        return 0.0
    nf = len(fields[0])
    cols = [[f[k] for f in fields] for k in range(nf)]
    best = 0.0
    for a in range(nf):
        if not _offset_like(cols[a], limit):
            continue
        best = max(best, 0.25)             # an offset column on its own
        for b in range(nf):
            if b == a:
                continue
            hits = sum(1 for i in range(len(fields) - 1)
                       if cols[a][i] + cols[b][i] == cols[a][i + 1])
            best = max(best, hits / float(len(fields) - 1 or 1))
    return best


def _fixed_table(data, chain, stride, min_entries, limit):
    """The best table hypothesis this stride chain supports, or None.

    The width of the character field is not read off the format -- there is no
    format -- so it is measured, and measuring it needs more care than taking
    whichever value wins a vote.

    Not the smallest: a field is a fixed size hundreds of records agree on, but
    any single measurement can come up short, and one record of Quake's 339 has
    a byte in its name field that is neither printable nor padding. The minimum
    let that record redefine a 56-byte field as a 10-byte one.

    Not simply the commonest either, because a chain does not always cover one
    population. Where it runs from a directory into the payloads behind it the
    votes are split between two real widths, and a single dirty record is then
    enough to swing which one leads. So the plausible widths are all tried and
    judged on what they produce -- a width is a hypothesis like the stride and
    the association, and the same evidence settles it.

    A KNOWN LIMIT. The vote only sees widths some record actually pads out to,
    and a writer that leaves its name buffer dirty pads out to none of them.
    Quake's shipped pak2 is the case: 56 is the true width and not one of its
    58 records votes for it, so the widths tried are 22, 23 and 20, and four
    names come back cut short. The score already knows better -- 56 scores
    0.5800 against 0.2500 for the winner -- it is simply never offered the
    hypothesis. Two fixes were measured over 32 shipped archives and both
    rejected: trying every structurally possible width (stride - 4k) is right
    for all of them but costs 4.7x wall time for that one archive, and ruling
    out any width that would truncate a name the chain plainly contains breaks
    the four big Doom IWADs, whose chains run past the directory into data that
    reads as longer names.
    """
    cap = min(stride - 4, 255)
    if cap < _MIN_NAME:
        return None
    seen_widths = {}
    for p in chain:
        w = _padded_width(data, p, cap)
        if w:
            seen_widths[w] = seen_widths.get(w, 0) + 1
    if not seen_widths:
        return None
    floor = max(1, len(chain) * _WIDTH_SUPPORT)
    widths = sorted((w for w, n in seen_widths.items()
                     if n >= floor and w >= _MIN_NAME),
                    key=lambda w: (seen_widths[w], w), reverse=True)
    best = None
    for width in widths[:_WIDTHS_TRIED]:
        table = _table_at_width(data, chain, stride, width, min_entries, limit)
        if table is not None and (best is None
                                  or _fixed_score(table) > _fixed_score(best)):
            best = table
    return best


def _table_at_width(data, chain, stride, width, min_entries, limit):
    """One stride chain read with one character-field width, or None."""
    got = _longest_valid(data, chain, width, min_entries)
    if got is None:
        return None
    first, names = got
    positions = chain[first:first + len(names)]
    nfields = (stride - width) // 4
    if nfields < 1:
        return None

    uniq = len(set(names)) / float(len(names))
    if uniq < _UNIQ_FLOOR:
        return None
    repeat = max(collections.Counter(names).values()) / float(len(names))
    if repeat > _REPEAT_CEIL:
        return None
    # Padding is measured, not gated. A floor here rejected nothing: a chain
    # only exists because four consecutive NUL-terminated names proposed it and
    # the walk held a padding ratio the whole way, so by this point the evidence
    # has already been demanded twice. Measured over 300 real audio files and
    # every specimen in this suite, adding the gate back changes no result --
    # and a guard that never decides anything is worse than no guard, because it
    # reads like a defence while doing nothing. It survives as a term in the
    # score below, where it ranks rather than rejects.
    padded = sum(1 for nm in names if len(nm) < width) / float(len(names))

    # Which side of the name the integers sit on. Quake writes name-then-fields,
    # Half-Life and Doom write fields-then-name, and the same bytes read as
    # either -- the difference is only which name they are paired with. Whichever
    # reading produces columns that behave like offsets and sizes is the one the
    # archive meant; when neither does, name-first is the commoner layout and the
    # payload check downstream still has the last word.
    variants = []
    for assoc, off in (("after", width), ("before", width - stride)):
        fields = _fields_at(data, positions, off, nfields)
        if fields is None:
            continue
        variants.append((_pairing_score(fields, limit), assoc, fields, off))
    if not variants:
        return None
    variants.sort(key=lambda v: (v[0], v[1] == "after"), reverse=True)
    score, assoc, fields, foff = variants[0]

    entries = []
    for p, nm, fl in zip(positions, names, fields):
        rec = p if assoc == "after" else p + foff
        entries.append({"offset": rec, "name": nm.decode("utf-8", "replace"),
                        "fields": fl, "name_at": p, "end": rec + stride})
    return {"entries": entries, "shape": "fixed-width", "stride": stride,
            "name_width": width, "name_offset": 0 if assoc == "after"
            else stride - width, "fields": nfields, "assoc": assoc,
            "offset": entries[0]["offset"], "pairing": round(score, 3),
            "uniq": round(uniq, 3), "padded": round(padded, 3),
            "repeat": round(repeat, 3)}


def _snap_start(entries, stride, limit):
    """How many leading records belong to the payloads, from the table's own sums.

    A stride walk cannot see where an index begins -- it stops when the bytes in
    front stop looking like records, and in freedoom1.wad they keep looking like
    records for another eighteen. Searching a few possibilities would not reach
    that, but nothing has to be searched: an archive that writes its payloads
    first and its index last has its last payload end exactly where the index
    begins. So read the end off the table's own last entry, and if it lands on
    one of this table's record boundaries, that boundary IS the start.

    Returns the number of records to drop, or 0 when the sums say nothing.
    """
    n = len(entries)
    nf = len(entries[0]["fields"])
    start = entries[0]["offset"]
    span = n * stride
    last = entries[-1]["fields"]
    best = None
    for ko in range(nf):
        for ks in range(nf):
            if ks == ko or last[ko] < 0 or last[ks] < 0:
                continue
            end = last[ko] + last[ks]
            if not (start < end < start + span) or (end - start) % stride:
                continue
            lead = (end - start) // stride
            if not lead or n - lead < _MIN_ENTRIES:
                continue
            # Only the last entry was needed to propose the boundary; the whole
            # column is what confirms it. Reading the proposal off records that
            # are themselves in doubt is how the junk in front would get to vote
            # on where the junk in front ends.
            if not _offset_like([e["fields"][ko] for e in entries[lead:]],
                                limit):
                continue
            if best is None or lead < best:
                best = lead
    return best or 0


def _fixed_score(table):
    """Rank candidates before anything has been verified against payloads."""
    n = len(table["entries"])
    return (min(1.0, n / 200.0) * table["uniq"]
            * (0.5 + 0.5 * table["padded"]) * (1.0 + table["pairing"]))


def fixed_candidates(data, min_entries=_MIN_ENTRIES, limit=None, top=6):
    """Ranked fixed-width table hypotheses, best first.

    More than one is returned on purpose. Shipped archives contain other
    fixed-width tables -- a compiled symbol table in SW.GRP outscores the
    archive's own directory on shape alone, and a General MIDI instrument list
    inside a Doom WAD outscores nothing but looks exactly like an index. Shape
    cannot separate those from the real thing; only trying to place payloads
    with them can, so the choice is left to whoever can do that.
    """
    if limit is None:
        limit = len(data)
    out = []
    for chain, stride in _stride_chains(data, min_entries):
        table = _fixed_table(data, chain, stride, min_entries, limit)
        if table is not None:
            out.append(table)
    out.sort(key=_fixed_score, reverse=True)
    out = _one_per_region(out)
    for t in out:
        t["confidence"] = _confidence(t["entries"])
    return out[:top]


def _one_per_region(tables):
    """Keep the best reading of each stretch of bytes.

    Any table can also be read at twice its stride, which produces a second
    perfectly self-consistent table covering the same bytes with every other
    record. Both are real chains and the coarse one sometimes scores higher, so
    Quake's 339-entry directory lost to a 169-entry reading of itself. Within
    one region there is one index, and the reading that accounts for the most
    records is the one that explains the bytes -- a stride of 128 that only ever
    lands on even records is a worse theory than the 64 it is a multiple of.
    """
    kept = []
    for t in sorted(tables, key=lambda x: len(x["entries"]), reverse=True):
        lo, hi = t["offset"], t["entries"][-1]["end"]
        if any(lo < k["entries"][-1]["end"] and k["offset"] < hi for k in kept):
            continue
        kept.append(t)
    kept.sort(key=_fixed_score, reverse=True)
    return kept


def find_fixed_toc(data, min_entries=_MIN_ENTRIES, limit=None):
    """The best fixed-width table-of-contents hypothesis, or None."""
    got = fixed_candidates(data, min_entries, limit, top=1)
    return got[0] if got else None


# extension -> the magic its payload should open with. Only used to VERIFY a
# placement hypothesis, never to identify anything, so a short list of things
# acidcat already knows is enough.
def _ext_magics():
    from acidcat.core.infra import sniff
    out = {}
    for fmt, (magic, _kind) in sniff.AUDIO_CONTAINERS.items():
        out.setdefault("." + fmt, magic)
    out.update({".ogg": b"OggS", ".oga": b"OggS", ".mp3": b"ID3",
                ".wav": b"RIFF", ".flac": b"fLaC", ".aiff": b"FORM",
                ".aif": b"FORM", ".mid": b"MThd",
                # Creative Voice, the sample format of the DOS era: 331 of the
                # 456 entries in DUKE3D.GRP and 565 of 693 in SW.GRP are .voc,
                # so without it those two archives verify against nothing.
                ".voc": b"Creative Voice File"})
    return out


def _magic_for(name, magics):
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return magics.get(ext)


def _verify_offsets(fh, entries, offsets, magics):
    """(verified, checked) for entries placed at these absolute offsets."""
    verified = checked = 0
    for e, off in zip(entries, offsets):
        want = _magic_for(e["name"], magics)
        if not want or off < 0:
            continue
        checked += 1
        try:
            fh.seek(off)
            if fh.read(len(want)) == want:
                verified += 1
        except OSError:
            pass
    return verified, checked


def _file_size(fh):
    try:
        return os.fstat(fh.fileno()).st_size
    except (OSError, AttributeError):
        here = fh.tell()
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(here)
        return size


def _size_field(entries, offsets, skip, table_start, limit):
    """The column that reads as the size of the payload at `offsets`.

    Two things an index's own arithmetic must satisfy, and neither is equality.
    Payloads may not overlap each other, and the last one must end where the
    index begins -- or at the end of the file, for an archive that writes its
    index first.

    Demanding `offset[k] + size[k] == offset[k+1]` instead looks equivalent and
    is not: it assumes payloads are packed with no slack. Quake and Half-Life
    pack them, so it holds there and looked like a law. freedoom pads every lump
    up to a four-byte boundary, which satisfies neither equality nor any
    threshold near it -- its true directory scores 0.601 -- while the ordering
    is perfect and the closure exact to the byte. The rule that fits all of them
    is the weaker one, so that is the rule.
    """
    nf = len(entries[0]["fields"])
    n = len(entries)
    best = None
    for k in range(nf):
        if k == skip:
            continue
        col = [e["fields"][k] for e in entries]
        if any(v < 0 for v in col) or offsets[-1] + col[-1] > limit:
            continue
        # Only entries that HAVE an extent can overlap one. An index is allowed
        # to hold entries of size zero, and they are not damage: Doom's S_START,
        # F_END and their kin delimit runs of lumps and carry no data, so their
        # offset field points at nothing -- all 50 of DOOM.WAD's are literally
        # zero. Counting those as extents made six ordinary markers read as six
        # overlaps, which was enough to make dropping the last two score better
        # than keeping them, and the file came back with 2,304 of its 2,306.
        real = [i for i in range(n) if col[i] > 0]
        if len(real) < 2:
            continue
        room = sum(1 for a, b in zip(real, real[1:])
                   if offsets[a] + col[a] <= offsets[b])
        fits = room / float(len(real) - 1)
        if fits < _NO_OVERLAP:
            continue
        # "Ends where the index begins" has to mean at or just short of it. An
        # archive that pads its payloads pads the last one too, and that padding
        # sits between the final payload and the index -- so demanding equality
        # rejects exactly the aligned archives the non-overlap rule above was
        # widened to accept.
        #
        # And it has to be measured from the last entry that HAS an extent. An
        # index may end in entries of size zero -- Doom's F_END and S_END mark
        # the boundaries of a run of lumps and hold no data, so their offset
        # field is whatever the writer left there. Closing on one of those asks
        # a payload that does not exist to end in the right place, and it fails,
        # and the cheapest way to make it pass is to drop the markers: DOOM.WAD
        # came back with 2,304 of its 2,306 lumps, the two missing ones being
        # exactly F2_END and F_END.
        last = real[-1]
        end = offsets[last] + col[last]
        closes = any(0 <= edge - end <= _CLOSE_SLACK
                     for edge in (table_start, limit))
        score = fits * (1.0 if closes else _UNCLOSED)
        if best is None or score > best[1]:
            best = (k, score)
    return best if best else (None, 0.0)


def _place_absolute(fh, entries, nf, magics, limit, table_start):
    """Read one column as an absolute file offset, if any column reads that way.

    Returns (field, size_field, verified, checked, consistency, offsets) or None.
    """
    best = None
    for k in range(nf):
        col = [e["fields"][k] for e in entries]
        if not _offset_like(col, limit):
            continue
        verified, checked = _verify_offsets(fh, entries, col, magics)
        ks, consistency = _size_field(entries, col, k, table_start, limit)
        # either the payloads are where this says, or the columns do the
        # arithmetic an index does; both beat guessing, neither is assumed
        if not ((checked and verified >= max(1, checked * 0.5))
                or consistency >= _NO_OVERLAP):
            continue
        rate = verified / float(checked) if checked else 0.0
        cand = (k, ks, verified, checked, consistency, col)
        if best is None or rate + consistency > best[0]:
            best = (rate + consistency, cand)
    return best[1] if best else None


def _place_contiguous(fh, entries, nf, magics, data_start):
    """Read one column as a size, with payloads laid down after the index."""
    base = entries[-1]["end"] if data_start is None else data_start
    best = None
    for k in range(nf):
        off, verified, checked = base, 0, 0
        for e in entries:
            length = e["fields"][k]
            if length < 0:
                verified = checked = 0
                break
            want = _magic_for(e["name"], magics)
            if want and checked < _MAX_CHECKS:
                checked += 1
                try:
                    fh.seek(off)
                    if fh.read(len(want)) == want:
                        verified += 1
                except OSError:
                    pass
            off += length
        if checked and verified >= max(1, checked * 0.5):
            rate = verified / float(checked)
            if best is None or rate > best[0]:
                best = (rate, (k, verified, checked, base))
    return best[1] if best else None


def place_entries(fh, toc, data_start=None):
    """Give each table entry a byte offset, and check whether that is real.

    Two ways an archive says where a payload is. It may store the offset, in
    which case the answer is in the table and the only question is which column
    holds it; or it may store only sizes and write the payloads back to back
    after the index, in which case the offsets are derivable by accumulating.
    Both are tried, because which one an archive chose is not knowable from its
    bytes -- Quake stores offsets, Duke Nukem stores only sizes, and the two
    files are otherwise the same shape.

    Which column is which is decided by VERIFYING rather than assuming: entries
    whose names carry a known extension must land on that format's magic. A
    wrong column scores near zero immediately and a right one near 1.0, and the
    score is reported rather than swallowed. Where nothing carries a checkable
    extension -- a Doom WAD names its lumps and gives them no suffix at all --
    the fallback evidence is what the archive writer must have arranged while
    laying the payloads down: extents that do not overlap each other, and a last
    payload that ends where the index begins.

    Where the index stops and the rest of the file starts is the one thing a
    stride detector cannot see, at either end. The first bytes of the first
    payload are as likely to read as another record as anything else, and in
    DUKE3D.GRP the sixteen-byte header in front of the directory reads as one
    too: `KenSilverman` is a perfectly good twelve-character name followed by a
    perfectly good integer. Three records too many, one at the front and two at
    the back, and every derived offset is wrong by both a record and a size. So
    a few records at each edge may be handed back -- a bounded question with a
    checkable answer, rather than an assumption.

    Returns (entries, field_index, verified, checked) with `offset`/`length`
    added to each entry, or (entries, None, 0, 0) when nothing places them.
    `toc` gains "placement", "size_field" and "trimmed" describing what was
    decided, and its entry list is replaced by the one that was placed.
    """
    entries = toc["entries"]
    if not entries:
        return entries, None, 0, 0
    magics = _ext_magics()
    limit = _file_size(fh)
    nf = toc["fields"]

    # A fixed-width table can say where it begins, and it has to be asked here:
    # the detector works in the coordinates of the window it scanned, while the
    # integers it read are the archive's own absolute file offsets. Only this
    # layer holds both.
    if toc.get("stride"):
        lead = _snap_start(entries, toc["stride"], limit)
        if lead:
            entries = toc["entries"] = entries[lead:]
            toc["offset"] = entries[0]["offset"]
    n = len(entries)

    best = None
    for lead, trail in _EDGE_TRIMS:
        if n - lead - trail < _MIN_ENTRIES:
            continue
        kept = entries[lead:n - trail]
        got = _place_absolute(fh, kept, nf, magics, limit, toc["offset"])
        if got is not None:
            k, ks, verified, checked, consistency, col = got
            rate = verified / float(checked) if checked else 0.0
            rank = (rate + consistency, len(kept))
            if best is None or rank > best[0]:
                best = (rank, "absolute", kept, k, ks, verified, checked,
                        consistency, col, (lead, trail))
        got = _place_contiguous(fh, kept, nf, magics, data_start)
        if got is not None:
            k, verified, checked, base = got
            rank = (verified / float(checked), len(kept))
            if best is None or rank > best[0]:
                best = (rank, "contiguous", kept, k, None, verified, checked,
                        0.0, base, (lead, trail))
    if best is None:
        return entries, None, 0, 0

    (_rank, mode, kept, k, ks, verified, checked, consistency, extra,
     trimmed) = best
    if mode == "absolute":
        col = extra
        for i, e in enumerate(kept):
            e["offset"] = col[i]
            if ks is not None:
                e["length"] = e["fields"][ks]
            elif i + 1 < len(col):
                e["length"] = col[i + 1] - col[i]
            else:
                e["length"] = max(0, limit - col[i])
        toc["consistency"] = round(consistency, 3)
    else:
        off = extra
        for e in kept:
            e["offset"] = off
            e["length"] = e["fields"][k]
            off += e["length"]
        ks = k
    toc["entries"] = kept
    toc["placement"] = mode
    toc["size_field"] = ks
    toc["trimmed"] = trimmed
    # The search above stops reading payloads once it has seen enough to rank a
    # hypothesis, which makes its count a budget rather than a measurement. The
    # winner is then checked in full, because a number this reports gets read as
    # how much of the archive was confirmed, and "64 of 64" that really means
    # "the first 64 of 373" is the exact species of claim this module exists to
    # not make.
    verified, checked = _verify_offsets(
        fh, kept, [e["offset"] for e in kept], magics)
    return kept, k, verified, checked


# ---------------------------------------------------------------------------
# The whole job, for a caller holding a file
# ---------------------------------------------------------------------------
#
# An index is written wherever the format's author found it convenient, and
# "convenient" was usually the end: appending payloads as they are produced and
# writing the directory last means never seeking backwards. Of the archives
# measured here, Duke Nukem and tModLoader put it at the head and Quake, Doom
# and Half-Life put it within 450 KB of the tail. A head-only window is not a
# small oversight for this family, it misses most of it.
_HEAD_WINDOW = 2 << 20
_TAIL_WINDOW = 8 << 20


def read_toc(fh, head=_HEAD_WINDOW, tail=_TAIL_WINDOW, min_entries=_MIN_ENTRIES):
    """Find this file's index and place its payloads, or None.

    Reads a window at each end, runs both detectors over each, and settles the
    result by verification -- which is the only thing that can, because a
    shipped archive contains fixed-width tables that are not its directory. A
    compiled symbol table in SW.GRP is longer and tidier than the archive's own
    index; it places no payloads at all, and that is how it loses.

    Returns (toc, entries, field, verified, checked), where `toc` carries
    "window" describing which end it was found at, or None if no window held a
    table at all. A table that was found but could not be followed to its
    payloads comes back with `field` None rather than as nothing: "there is an
    index here and I cannot read where it points" is a different answer from
    "there is no index here", and collapsing the two would be this module
    reporting a limit of its own as a fact about the file.
    """
    size = _file_size(fh)
    windows = [(0, min(head, size))]
    if size > head + tail:
        windows.append((size - tail, tail))
    elif size > head:
        windows.append((head, size - head))

    scored = []
    unplaced = []
    for base, length in windows:
        try:
            fh.seek(base)
            data = fh.read(length)
        except OSError:
            continue
        if not data:
            continue
        cands = list(fixed_candidates(data, min_entries, limit=size))
        one = find_toc(data, min_entries)
        if one is not None:
            cands.append(one)
        for toc in cands:
            _rebase(toc, base)
            toc["window"] = "head" if base == 0 else "tail"
            entries, field, verified, checked = place_entries(fh, toc)
            if field is None:
                unplaced.append((len(entries), toc, entries))
                continue
            rate = verified / float(checked) if checked else 0.0
            scored.append(((rate, verified, len(entries)), toc, entries,
                           field, verified, checked))
    if scored:
        scored.sort(key=lambda s: s[0], reverse=True)
        _rank, toc, entries, field, verified, checked = scored[0]
        return toc, entries, field, verified, checked
    if unplaced:
        unplaced.sort(key=lambda u: u[0], reverse=True)
        _n, toc, entries = unplaced[0]
        return toc, entries, None, 0, 0
    return None


def _rebase(toc, base):
    """Shift a table found in a window to the file's own byte space.

    The integer FIELDS are left alone: they were written by the archive and
    already refer to the file. Only the positions this module computed move.
    """
    if not base:
        return
    toc["offset"] += base
    for e in toc["entries"]:
        e["offset"] += base
        e["name_at"] += base
        e["end"] += base
