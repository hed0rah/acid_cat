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
core/formats/ableton.py.

Live stores no tempo NUMBER: warp markers map seconds to beats, and the tempo
is derived from consecutive pairs. Where markers exist that derivation is
exact -- it reproduced the declared tempo of a Live Set on 40 of 40 clips. An
unwarped sample carries no markers and therefore no tempo, which is most of a
sample library: markers appear in about 3% of files, and in 879 of 879 clips
the Set marked unwarped, the sidecar had none.

The sections are OPTIONAL, not versioned. Do not read the presence of one as a
format generation -- 62% of files declare both the beat-tracking and overview
sets, and the same Live version writes sidecars with and without markers,
because a .asd is written when the audio is analysed and outlives projects.

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

# Optional sections, NOT format generations. An earlier version of this walker
# called these "older" and "newer" schema generations and printed that on every
# file. It was wrong: measured over 1,473 sidecars, 62% declare BOTH sets, so
# they are not exclusive and cannot be generations. The Live version that wrote
# the Set does not predict them either -- the `WarpMarker` section appears
# under Live 9.7 through 12, and one project written by a single version
# contains sidecars both with and without it, because a .asd is written when
# the audio is ANALYSED and outlives the projects that use it.
_SECTIONS = (
    ("beat tracking", ("InitialBPM", "Bpms", "BeatTrackState", "Tonalities",
                       "PitchMarks")),
    ("overview pyramid", ("OverViewLevels", "SamplesPerBinLog2")),
    ("tempo estimate", ("UnbiasedTempoEstimate", "AufTaktData")),
    ("warp markers", ("WarpMarker", "SecTime", "BeatTime")),
)


def _sections(names):
    """Which optional sections this sidecar declares."""
    got = [label for label, fields in _SECTIONS
           if any(f in names for f in fields)]
    return ", ".join(got) if got else "no optional sections"


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
        # the declarations sit at the front; the rest is the overview pyramid,
        # so bound the dictionary walk rather than scanning megabytes of peaks
        # scan to EOF, not a window: in 4.8% of files the overview pyramid
        # comes first and the declarations sit 10-25 KB deep, so a window
        # dropped their whole object tree. Costs about 8 ms on a 640 KB file.
        toks = abmod.type_dictionary(raw, body_off, len(raw), h["order"])
        tags = {n: t for kind, n, t in toks if kind == "field"}
        classes = [n for kind, n, _ in toks if kind == "class"]
        present = set(tags)
        notable = [n for n in abmod.NOTABLE_FIELDS if n in present]

        obj_fields = [
            _f(0, 0, "declared_fields", f"{len(present)} distinct",
               "the SCHEMA, not the contents -- see below"),
            _f(0, 0, "note", "declared != stored",
               "this schema is shared with the Live Set, so it names clip "
               "settings the sidecar does not carry. LoopEnd is declared here "
               "and absent from 91% of files whose Set states a real one: loop "
               "points belong to a clip, and one audio file can back many "
               "clips. What the sidecar holds is per-file ANALYSIS"),
            _f(0, 0, "declared_classes", f"{len(set(classes))} distinct",
               "u8 length + ASCII name"),
            _f(0, 0, "sections", _sections(present),
               "optional sections, not versions -- 62% of files declare both "
               "the beat-tracking and overview sets, so they cannot be "
               "generations of one another"),
        ]
        for n in notable:
            tag = tags.get(n)
            kind = abmod.TYPE_TAGS.get(tag, "unknown")
            obj_fields.append(_f(0, 0, n, kind, f"type tag 0x{tag:02x}"))
        chunks.append({
            "id": "objects", "offset": body_off, "size": size - body_off,
            "summary": (f"object tree, {len(present)} typed fields across "
                        f"{len(set(classes))} classes: {_sections(present)}"),
            "fields": obj_fields, "warnings": [], "payload_base": body_off,
        })
        if not present:
            # verified over 1,500 specimens: when the scan finds no field names
            # in EITHER byte order, the file genuinely carries only a header and
            # grid. Say that, rather than something that reads as a parse failure
            warns.append("this sidecar carries only the header and frame grid; "
                         "there is no object tree in it")
        elif not notable:
            warns.append(f"{len(present)} declared fields, none of them a "
                         f"recognised analysis field")

        # a .asd is named "<audio>.asd" and lives beside its audio, so when the
        # sibling is there its size can be checked against what Live recorded
        sibling = filepath[:-4] if filepath.lower().endswith(".asd") else None
        if sibling and os.path.exists(sibling):
            ssize = os.path.getsize(sibling)
            if not abmod.references_size(raw, ssize, h["order"]):
                warns.append(
                    f"this sidecar does not reference the current size of "
                    f"{os.path.basename(sibling)} ({ssize:,} bytes); the audio "
                    f"was changed after the analysis was written")

        marks = abmod.warp_markers(raw, h["order"])
        if marks:
            bpm = abmod.derived_tempo(marks)
            wf = [_f(0, 4, "count", len(marks), "warp markers")]
            for m in marks[:8]:
                wf.append(_f(0, 16, f"marker[{m['id']}]",
                             f"{m['sec']:.6f} s = beat {m['beat']:g}"))
            if bpm:
                wf.append(_f(0, 0, "derived_tempo", f"{bpm:g} BPM",
                             "beats per second between two markers x 60; Live "
                             "stores this mapping, not the number"))
            chunks.append({
                "id": "warp", "offset": raw.find(abmod.WARP_MARKER_NAME),
                "size": len(marks) * abmod.WARP_MARKER_SIZE,
                "summary": (f"{len(marks)} warp marker(s)"
                            + (f", {bpm:g} BPM" if bpm else "")),
                "fields": wf, "warnings": [],
                "payload_base": raw.find(abmod.WARP_MARKER_NAME),
            })

        on = abmod.onsets(raw, h["total_frames"], body_off, h["order"])
        if on:
            rate = h["sample_rate"]
            first, last = on["positions"][0], on["positions"][-1]
            onf = [
                _f(0, 4, "count", on["count"], "detected transients"),
                _f(0, 4, "first", f"{first:,} frames"
                   + (f" = {first / rate:.3f} s" if rate else "")),
                _f(0, 4, "last", f"{last:,} frames"
                   + (f" = {last / rate:.3f} s" if rate else "")),
                _f(0, 0, "positions", ", ".join(f"{p:,}" for p in on["positions"][:12])
                   + (" ..." if on["count"] > 12 else "")),
                _f(0, 0, "energies", ", ".join(f"{e:g}" for e in on["energies"][:8])
                   + (" ..." if on["count"] > 8 else ""),
                   "TransitionEnergies, one per onset"),
            ]
            chunks.append({
                "id": "onsets", "offset": on["offset"],
                "size": on["end"] - on["offset"],
                "summary": (f"{on['count']} transient(s), "
                            f"{first:,} to {last:,} frames"),
                "fields": onf, "warnings": [], "payload_base": on["offset"],
            })

            cp = abmod.clip_params(raw, on["offset"], h["order"])
            if cp:
                chunks.append({
                    "id": "clip", "offset": cp["offset"],
                    "size": 4 * len(abmod.CLIP_PARAMS),
                    "summary": "warp-engine parameters",
                    "fields": [_f(4 * i, 4, n, cp["values"][n])
                               for i, (n, _k) in enumerate(abmod.CLIP_PARAMS)],
                    "warnings": [], "payload_base": cp["offset"],
                })

        ov = abmod.overview_trailer(raw, h["order"])
        if ov:
            # channels is the one value here proven against independent ground
            # truth: it matched the source audio on 419 of 419 files that had one
            per = ov["bin_samples"]
            bins = ((h["total_frames"] + per - 1) // per
                    if (h["total_frames"] and per) else 0)
            ovf = [
                _f(0, 4, "channels", ov["channels"],
                   "verified against the source audio on 419/419 specimens"),
                _f(0, 4, "bytes_per_bin", ov["bytes_per_bin"],
                   "channels x 2 -- one int16 per channel per bin"),
                _f(0, 4, "samples_per_bin_log2", ov["samples_per_bin_log2"],
                   "read from the file, not inferred"),
            ]
            if per:
                ovf.append(_f(0, 0, "bin_samples", f"{per:,}",
                              "1 << the log2 above"))
                ovf.append(_f(0, 0, "bins", f"{bins:,}",
                              "total_frames / bin_samples"))
            if not ov["consistent"]:
                warns.append(
                    f"overview bytes_per_bin is {ov['bytes_per_bin']}, expected "
                    f"{ov['channels'] * 2} for {ov['channels']} channel(s)")
            chunks.append({
                "id": "overview", "offset": ov["sentinel_at"], "size": 4,
                "summary": (f"waveform overview, {ov['channels']} channel(s) at "
                            + (f"{per:,} samples/bin" if per
                               else "an unreadable bin size")),
                "fields": ovf, "warnings": [], "payload_base": ov["sentinel_at"],
            })

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
