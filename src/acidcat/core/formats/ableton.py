"""Ableton Live container primitives.

Two unrelated shapes ship under the Live umbrella:

* ``.asd`` -- the binary per-sample analysis sidecar. Reverse-engineered here
  against 8,196 real specimens; see ``parse_asd_header``.
* ``.als`` / ``.alc`` / ``.adg`` / ``.adv`` / ``.alp`` -- gzipped XML, with the
  document type given by the root element's first child.

``.amxd`` (Max for Live) is a third, chunked ``ampf`` container.

Nothing here reads a whole file into memory unbounded: the XML side is a
gzip stream that expands ~50x in the wild, so every decompression is capped.
"""

import gzip
import re
import struct
import zlib

# byte 0 is always 0x06; byte 1 is the TIFF-style byte-order mark, 'I' for
# Intel (little-endian) and 'M' for Motorola (big-endian). Measured over 8,196
# specimens: 7,748 little, 319 big, and the big ones parse only big-endian.
ASD_MAGIC_LE = b"\x06\x49"
ASD_MAGIC_BE = b"\x06\x4d"
ASD_MAGICS = (ASD_MAGIC_LE, ASD_MAGIC_BE)

# an AppleDouble resource stub named "._foo.wav.asd" is not a .asd at all. 129
# of the 8,196 files carrying the extension were these. Recognising them stops
# a corpus sweep reporting them as corrupt Ableton files.
APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"

# the frame-position table steps by at most 30 ms of audio, so the largest step
# pins the sample rate. Ordered so the first grain >= the observed step wins.
_STANDARD_RATES = (8000, 11025, 16000, 22050, 32000, 44100, 48000,
                   88200, 96000, 176400, 192000)
ASD_GRAIN_SECONDS = 0.03

# a Live Set decompresses to several MB of XML; a hostile or merely huge one
# should not be able to force an unbounded allocation.
XML_DECOMPRESS_CAP = 64 * 1024 * 1024

# field names are stored as u32 character-count + UTF-16LE. 69 distinct names
# were recovered across the specimen set.
_UTF16_RUN = re.compile(rb"(?:[ -~]\x00){3,}")

# fields that mark what the analysis actually contains, for the summary line
NOTABLE_FIELDS = (
    "WarpMarkers", "IsWarped", "WarpMode", "LoopStart", "LoopEnd",
    "HiddenLoopStart", "SampleOffset", "Numerator", "Denominator",
    "OriginalFileSize", "InitialBPM", "Bpms", "UnbiasedTempoEstimate",
    "OnSets", "UserOnsets", "PitchMarks", "Tonalities", "BeatTrackState",
    "OverViewLevels", "AufTaktData", "SamplesPerBinLog2",
)


class AbletonError(Exception):
    """Malformed enough that no structure can be reported."""


def is_appledouble(head):
    return head[:4] == APPLEDOUBLE_MAGIC


def asd_byte_order(head):
    """'<' or '>' for a .asd header, else None."""
    if head[:2] == ASD_MAGIC_LE:
        return "<"
    if head[:2] == ASD_MAGIC_BE:
        return ">"
    return None


def looks_like_asd(head, size=None):
    """Cheap structural check used by the sniffer.

    Two magic bytes alone would be far too weak, so the reserved u32 at offset
    6 (zero in all 8,067 real specimens) and a plausible table length carry
    most of the confidence.
    """
    order = asd_byte_order(head)
    if order is None or len(head) < 10:
        return False
    if struct.unpack_from(order + "I", head, 6)[0] != 0:
        return False
    count = struct.unpack_from(order + "I", head, 2)[0]
    if not 1 <= count < 10_000_000:
        return False
    return size is None or 10 + 4 * (count - 1) <= size


def infer_sample_rate(max_step):
    """(rate, exact) inferred from the largest step in the frame table.

    Steps are capped at 30 ms of audio, so the true rate is the smallest
    standard rate whose grain is not smaller than the largest observed step.
    `exact` is True only when the cap was actually reached -- a short or very
    steady file may never hit it, and then the rate is a lower bound rather
    than a reading. Returns (None, False) when no standard rate is big enough.
    """
    if max_step <= 0:
        return None, False
    for rate in _STANDARD_RATES:
        grain = round(rate * ASD_GRAIN_SECONDS)
        if grain >= max_step:
            return rate, grain == max_step
    return None, False


def parse_asd_header(raw):
    """Decode the fixed head of a .asd.

    Layout, confirmed against the source audio for 22 specimens and
    structurally against 8,067:

        0   u8[2]  magic 06 'I' | 06 'M' (byte order)
        2   u32    count N
        6   u32    reserved, zero
        10  u32[N-1] frame positions, strictly increasing, last == total
                     frames of the source audio, step <= 30 ms of audio
    """
    order = asd_byte_order(raw)
    if order is None:
        raise AbletonError("not an Ableton .asd (bad magic)")
    if len(raw) < 10:
        raise AbletonError("truncated before the .asd header")
    count = struct.unpack_from(order + "I", raw, 2)[0]
    reserved = struct.unpack_from(order + "I", raw, 6)[0]
    n = max(count - 1, 0)
    avail = (len(raw) - 10) // 4
    truncated = n > avail
    n = min(n, avail)
    frames = list(struct.unpack_from(f"{order}{n}I", raw, 10)) if n else []

    steps = [b - a for a, b in zip(frames, frames[1:])]
    monotonic = all(s > 0 for s in steps)
    max_step = max(steps) if steps else 0
    rate, exact = infer_sample_rate(max_step)
    total = frames[-1] if frames else 0
    return {
        "byte_order": "little" if order == "<" else "big",
        "order": order,
        "count": count,
        "reserved": reserved,
        "frames": frames,
        "table_end": 10 + 4 * n,
        "total_frames": total,
        "max_step": max_step,
        "sample_rate": rate,
        "rate_exact": exact,
        "duration": (total / rate) if (rate and total) else None,
        "monotonic": monotonic,
        "truncated": truncated,
    }


def field_names(raw, start=0):
    """[(offset, name)] for every u32-length-prefixed UTF-16LE field name.

    These are the serialised object-tree field names. They are interleaved with
    their data rather than gathered in a header, so they double as a map of
    what the file records. The length prefix is what separates a real name from
    an accidental run of ASCII-range UTF-16.
    """
    out = []
    for m in _UTF16_RUN.finditer(raw, start):
        off = m.start()
        if off < 4:
            continue
        try:
            declared = struct.unpack_from("<I", raw, off - 4)[0]
        except struct.error:
            continue
        text = m.group().decode("utf-16le")
        if declared == len(text):
            out.append((off - 4, text))
    return out


def class_names(raw, start=0):
    """[(offset, name)] for the length-prefixed ASCII class names."""
    out = []
    for m in re.finditer(rb"[A-Za-z][A-Za-z0-9_<>&]{3,40}", raw[start:]):
        off = start + m.start()
        if off >= 1 and raw[off - 1] == len(m.group()):
            out.append((off - 1, m.group().decode("ascii")))
    return out


# ── the gzipped-XML family ────────────────────────────────────────────────

# The element directly inside <Ableton> names the document type -- with one
# ambiguity that cannot be resolved from content: a Live Set (.als) and a Live
# Clip (.alc) BOTH use <LiveSet>, so only the extension separates them. A
# device preset (.adv) puts the device's own class there (<Operator>,
# <Wavetable>, ...), so it is the default rather than an enumerated case.
XML_ROOT_CHILDREN = {
    "LiveSet": "als",               # or alc -- see sniff_gzip_ableton
    "GroupDevicePreset": "adg",
}
_XML_LABELS = {
    "als": "Live Set",
    "alc": "Live Clip",
    "adg": "device group / rack",
    "adv": "device preset",
}


def xml_label(fmt_id):
    return _XML_LABELS.get(fmt_id, "Live document")


def gunzip_capped(path, cap=XML_DECOMPRESS_CAP):
    """Decompress at most `cap` bytes. Returns (data, truncated).

    A Live Set expands roughly fifty-fold, so an uncapped read is a memory
    hazard on hostile input and merely wasteful on ordinary input.
    """
    data = bytearray()
    with open(path, "rb") as fh:
        dec = zlib.decompressobj(zlib.MAX_WBITS | 16)
        while len(data) < cap:
            block = fh.read(65536)
            if not block:
                break
            try:
                data += dec.decompress(block, cap - len(data))
            except zlib.error as exc:
                raise AbletonError(f"gzip stream is damaged: {exc}") from exc
            if dec.eof:
                break
    return bytes(data[:cap]), len(data) >= cap


_ROOT_CHILD = re.compile(rb"<Ableton\b[^>]*>\s*<(\w+)")


def sniff_gzip_ableton(path):
    """Format id for a gzipped Ableton document, or None.

    Only the first block is decompressed -- enough to see the XML declaration
    and the root's first child without paying for a multi-megabyte set.

    A Live Pack (.alp) is also gzip but is an archive, not an XML document; it
    has no <Ableton> element and so returns None here rather than being
    mislabelled as a set.
    """
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(4096)
    except (OSError, EOFError, zlib.error):
        return None
    if b"<Ableton" not in head:
        return None
    m = _ROOT_CHILD.search(head)
    child = m.group(1).decode("ascii") if m else ""
    fid = XML_ROOT_CHILDREN.get(child, "adv")
    # <LiveSet> means set or clip; nothing in the content distinguishes them
    if fid == "als" and path.lower().endswith(".alc"):
        return "alc"
    return fid


_ATTR = re.compile(rb'(\w+)="([^"]*)"')


def header_attributes(xml_head):
    """Attributes of the root <Ableton> element, as a dict of str."""
    m = re.search(rb"<Ableton\b([^>]*)>", xml_head)
    if not m:
        return {}
    return {k.decode("ascii"): v.decode("utf-8", "replace")
            for k, v in _ATTR.findall(m.group(1))}
