"""Ableton Live walkers: the .asd analysis sidecar and the gzipped-XML family.

`.asd` is the interesting one and the only part that needed reverse
engineering. It sits beside an audio file as `<name>.wav.asd` and holds Live's
analysis of that audio: the waveform overview grid, warp markers, loop points,
onsets and time signature. It is NOT a peak cache, despite being commonly
described as one.

The single most useful thing it yields is that an ORPHANED sidecar still
describes audio that is gone. The frame-position grid ends at the source's
exact total frame count and steps by at most 30 ms, so the sample rate, frame
count and duration of a deleted file are all recoverable from the sidecar
alone. Nothing else in the toolchain can do that.

Layout and the 30 ms grain were derived here against 8,196 real specimens and
verified frame-for-frame against the source audio on 22 of them; see
core/formats/ableton.py. Live's own tempo is NOT stored as a number -- warp
markers are (sample position, beat time) pairs and tempo is derived from them,
so an unwarped sample records no tempo at all.

The `.als`/`.alc`/`.adg`/`.adv`/`.alp` family is gzipped XML and needs no
reverse engineering; it is walked here so the whole Ableton footprint in a
sample library reports as one thing.
"""

import os
import struct

from acidcat.core.formats import ableton as abmod
from acidcat.core.walk.base import _f

# a .asd is a few hundred KB at most in the wild (largest of 8,196: 648 KB).
# capped so a forged count cannot make us allocate on a hostile file.
_ASD_READ_CAP = 64 * 1024 * 1024

# the two schema generations, told apart by fields only one of them declares
_GEN_OLD = ("InitialBPM", "Bpms", "BeatTrackState", "Tonalities", "PitchMarks")
_GEN_NEW = ("UnbiasedTempoEstimate", "OverViewLevels", "AufTaktData",
            "SamplesPerBinLog2", "PreprocessedDataChunk")


def _generation(names):
    """Which serialisation generation this file is, from the fields present."""
    old = sum(1 for n in _GEN_OLD if n in names)
    new = sum(1 for n in _GEN_NEW if n in names)
    if new > old:
        return "newer (overview pyramid, UnbiasedTempoEstimate)"
    if old > new:
        return "older (beat-track state, InitialBPM)"
    return "indeterminate"


def inspect_asd(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as fh:
        raw = fh.read(_ASD_READ_CAP)
    warns = []

    if abmod.is_appledouble(raw):
        # 129 of 8,196 files carrying the .asd extension were these. Saying so
        # is more useful than reporting a corrupt Ableton file.
        return [{"id": "AppleDouble", "offset": 0, "size": size,
                 "summary": ("macOS AppleDouble resource stub, not an Ableton "
                             "sidecar -- a '._' file copied off an HFS volume"),
                 "fields": [], "warnings": [], "payload_base": 0}], \
            ["file carries the .asd extension but is an AppleDouble stub"]

    h = abmod.parse_asd_header(raw)
    if h["truncated"]:
        warns.append(f"frame grid claims {h['count'] - 1:,} entries but the "
                     f"file holds only {(size - 10) // 4:,}")
    if not h["monotonic"]:
        warns.append("frame grid is not strictly increasing; positions are unreliable")
    if h["reserved"]:
        warns.append(f"reserved u32 at offset 6 is {h['reserved']}, expected 0")

    order_note = ("little-endian ('I', Intel)" if h["order"] == "<"
                  else "big-endian ('M', Motorola -- a PowerPC-era Mac file)")
    header = {
        "id": "hdr", "offset": 0, "size": min(size, 10),
        "summary": f"Ableton analysis sidecar, {order_note}",
        "fields": [
            _f(0x00, 1, "marker", "0x06", "constant across every specimen"),
            _f(0x01, 1, "byte_order", chr(raw[1]) if len(raw) > 1 else "?",
               "'I' little-endian / 'M' big-endian, the TIFF convention"),
            _f(0x02, 4, "count", f"{h['count']:,}",
               "grid entries + 1", enc=h["order"] + "I", raw=h["count"]),
            _f(0x06, 4, "reserved", h["reserved"], "zero in all 8,067 specimens",
               enc=h["order"] + "I", raw=h["reserved"]),
        ],
        "warnings": [], "payload_base": 0,
    }

    rate, dur = h["sample_rate"], h["duration"]
    if rate is None:
        grid_summary = (f"{len(h['frames']):,} positions, ends at "
                        f"{h['total_frames']:,} frames (sample rate not inferable)")
        warns.append("largest grid step exceeds 30 ms at every standard sample "
                     "rate; the rate could not be inferred")
    else:
        approx = "" if h["rate_exact"] else " (lower bound -- grid never hit the cap)"
        grid_summary = (f"{len(h['frames']):,} positions over {h['total_frames']:,} "
                        f"frames = {dur:.3f} s at {rate:,} Hz{approx}")

    grid_fields = [
        _f(0, 4, "first_position", f"{h['frames'][0]:,}" if h["frames"] else "-",
           "frames"),
        _f(max(0, len(h["frames"]) - 1) * 4, 4, "last_position",
           f"{h['total_frames']:,}",
           "equals the source audio's total frame count"),
    ]
    if h["max_step"]:
        grid_fields.append(_f(0, 0, "max_step", f"{h['max_step']:,} frames",
                              "grid steps are capped at 30 ms of audio, which "
                              "is what pins the sample rate"))
    if rate:
        grid_fields.append(_f(0, 0, "inferred_rate", f"{rate:,} Hz",
                              "exact" if h["rate_exact"] else
                              "lower bound; a short file may never reach the cap"))
        grid_fields.append(_f(0, 0, "duration", f"{dur:.3f} s",
                              "last_position / inferred_rate"))

    grid = {"id": "grid", "offset": 10, "size": h["table_end"] - 10,
            "summary": grid_summary, "fields": grid_fields,
            "warnings": [], "payload_base": 10}
    chunks = [header, grid]

    body_off = h["table_end"]
    if body_off < len(raw):
        names = abmod.field_names(raw, body_off)
        present = {n for _, n in names}
        notable = [n for n in abmod.NOTABLE_FIELDS if n in present]
        obj_fields = [
            _f(0, 0, "field_names", f"{len(present)} distinct",
               "stored as u32 char-count + UTF-16LE, interleaved with their data"),
            _f(0, 0, "generation", _generation(present),
               "no global version byte; the field set is the tell"),
        ]
        for n in notable:
            off = next(o for o, nm in names if nm == n) - body_off
            obj_fields.append(_f(off, 0, n, "present"))
        chunks.append({
            "id": "objects", "offset": body_off, "size": size - body_off,
            "summary": (f"serialised object tree, {len(present)} field names, "
                        f"{_generation(present)}"),
            "fields": obj_fields, "warnings": [], "payload_base": body_off,
        })
        if not notable:
            warns.append("no recognised analysis fields found in the object tree")

    return chunks, warns


# ── the gzipped-XML family ────────────────────────────────────────────────

_COUNTED = (
    (b"<AudioTrack", "audio tracks"),
    (b"<MidiTrack", "MIDI tracks"),
    (b"<ReturnTrack", "return tracks"),
    (b"<AudioClip", "audio clips"),
    (b"<MidiClip", "MIDI clips"),
    (b"<SampleRef", "sample references"),
    (b"<PluginDevice", "plugin devices"),
    (b"<AuPluginDevice", "AU plugin devices"),
)


def inspect_ableton_xml(filepath, fmt_id="als"):
    """Walk a gzipped Ableton XML document (.als/.alc/.adg/.adv)."""
    label = abmod.xml_label(fmt_id)
    size = os.path.getsize(filepath)
    warns = []
    try:
        xml, truncated = abmod.gunzip_capped(filepath)
    except abmod.AbletonError as exc:
        return [], [str(exc)]
    if truncated:
        warns.append(f"XML exceeded the {abmod.XML_DECOMPRESS_CAP // (1024 * 1024)} MB "
                     f"decompression cap; counts below describe only the prefix read")

    attrs = abmod.header_attributes(xml[:4096])
    if not attrs:
        warns.append("no <Ableton> root element found in the decompressed XML")

    ratio = (len(xml) / size) if size else 0
    fields = [_f(0, 0, k, v) for k, v in attrs.items()]
    fields.append(_f(0, 0, "decompressed", f"{len(xml):,} bytes",
                     f"{ratio:.1f}x the {size:,} bytes on disk"))
    root = {"id": "Ableton", "offset": 0, "size": size,
            "summary": (f"{label}, written by "
                        f"{attrs.get('Creator', 'an unknown Live version')}"),
            "fields": fields, "warnings": [], "payload_base": 0}

    counts = [(lab, xml.count(tag)) for tag, lab in _COUNTED]
    body_fields = [_f(0, 0, lab, f"{n:,}") for lab, n in counts if n]
    body = {"id": "content", "offset": 0, "size": size,
            "summary": ", ".join(f"{n:,} {lab}" for lab, n in counts if n)
                       or "no recognised Live elements",
            "fields": body_fields, "warnings": [], "payload_base": 0}
    return [root, body], warns


# ── Max for Live ──────────────────────────────────────────────────────────

# 'ampf' magic, a u32 version, then a 4-byte marker -- 'aaaa' in every specimen
# seen -- and only then the chunk chain. Reading the marker as a chunk id makes
# its next 4 bytes look like a 1.6 GB length, which is how this was caught.
_AMXD_HEADER = 12
_AMXD_MAX_CHUNKS = 64


def inspect_amxd(filepath):
    """Walk a Max for Live device: an 'ampf' header then an id/length chain."""
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as fh:
        raw = fh.read(_ASD_READ_CAP)
    warns = []
    if raw[:4] != b"ampf":
        warns.append("missing 'ampf' magic")
    marker = raw[8:12]
    if marker != b"aaaa":
        warns.append(f"marker at offset 8 is {marker!r}, expected b'aaaa'")

    chunks = [{
        "id": "ampf", "offset": 0, "size": min(size, _AMXD_HEADER),
        "summary": "Ableton Max Patch Format header",
        "fields": [
            _f(0x00, 4, "magic", "ampf"),
            _f(0x04, 4, "version",
               struct.unpack_from("<I", raw, 4)[0] if len(raw) >= 8 else "?"),
            _f(0x08, 4, "marker", marker.decode("ascii", "replace"),
               "constant separator before the chunk chain"),
        ],
        "warnings": [], "payload_base": 0,
    }]

    # chunk chain: 4-byte id + 4-byte little-endian length
    off = _AMXD_HEADER
    seen = 0
    while off + 8 <= len(raw) and seen < _AMXD_MAX_CHUNKS:
        cid = raw[off:off + 4]
        length = struct.unpack_from("<I", raw, off + 4)[0]
        if length > len(raw) - off - 8:
            warns.append(f"chunk '{cid.decode('ascii', 'replace')}' at {off} "
                         f"claims {length:,} bytes, past end of file")
            break
        name = cid.decode("ascii", "replace")
        summary = f"{length:,} bytes"
        if cid == b"ptch":
            # the patcher: a short binary preamble, then the Max patch as JSON
            body = raw[off + 8:off + 8 + min(length, 4096)]
            brace = body.find(b"{")
            summary = (f"Max patcher, {length:,} bytes"
                       + (f", JSON at +{brace}" if brace >= 0 else ", no JSON found"))
        chunks.append({
            "id": name, "offset": off, "size": length + 8,
            "summary": summary, "fields": [], "warnings": [],
            "payload_base": off + 8,
        })
        off += 8 + length
        seen += 1
    if seen >= _AMXD_MAX_CHUNKS:
        warns.append(f"stopped after {_AMXD_MAX_CHUNKS} chunks; the chain may continue")
    elif off != size and not warns:
        warns.append(f"chunk chain ends at {off:,} but the file is {size:,} bytes")
    return chunks, warns
