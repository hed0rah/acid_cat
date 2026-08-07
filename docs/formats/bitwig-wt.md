# vawt Wavetable (.wt) Format Internals

Low-level reference for `vawt` wavetable files, the format Surge / Surge XT
reads and writes and that Bitwig Studio's Wavetable and Polymer devices use.

Primary source: Surge's own byte-level spec, `doc/WT fileformat.txt` in
`github.com/surge-synthesizer/surge`, cross-checked against `wt_header` /
`wtflags` in `src/common/dsp/Wavetable.h` and the maintainers' `wt-tool.py`.
Verified here against 152 real files.

---

## Overview

A `.wt` file is a `vawt` container: a fixed 12-byte little-endian header
followed by one or more single-cycle waveforms ("frames") stacked end to end,
which a wavetable oscillator scans through.

It is its own container, not a RIFF chunk; the `vawt` magic sits at byte 0.
(Do not confuse it with the `BWBM` beat-map chunk Bitwig stores *inside* WAV
files; that is a chunk within RIFF, this is a standalone format.)

**The `.wt` extension is not a format.** At least three unrelated things use
it, so extension-based dispatch is wrong here:

| producer | magic | shape |
|---|---|---|
| Surge, Bitwig | `vawt` | this format |
| Arturia Pigments | `<?xm` | XML, no relation |
| Dune 3 | none | a different binary header with no magic at all |

acidcat sniffs content, not extension, so the Arturia files are refused rather
than mis-walked.

### On the Bitwig attribution

Surge is the primary-source-verified producer. Bitwig is **corroborated but not
byte-verified here**: third-party tooling parses Surge and Bitwig `.wt` with one
shared parser and the identical struct, and Bitwig documents its Wavetable
device as importing `.wt` interchangeably. No Bitwig-authored specimen was
available to hex-diff. Treat it as very likely, not proven.

---

## File Structure

```
+-------------------------------------------+
| Header (12 bytes, little-endian)          |
|   "vawt" + frame_samples + frame_count    |
|   + flags                                 |
+-------------------------------------------+
| Sample data                               |
|   frame_count * frame_samples samples,    |
|   float32 LE, or int16 LE if flag 0x04,   |
|   frame-major (wave 0, then wave 1, ...)  |
+-------------------------------------------+
| <wtmeta> XML + NUL   (only if flag 0x10)  |
+-------------------------------------------+
```

Data always begins at byte 12. There is no offset field.

### Header

```
offset  size  field           notes
0       4     "vawt"          magic bytes (ASCII)
4       4     frame_samples   uint32 LE: samples in one single-cycle wave
8       2     frame_count     uint16 LE: number of waves in the table
10      2     flags           uint16 LE: bitfield, see below
```

```c
struct wt_header {          // 12 bytes, little-endian, packed
    char     magic[4];      // "vawt"
    uint32_t frame_samples; // samples per single-cycle wave (256 / 1024 / 2048)
    uint16_t frame_count;   // number of waves stacked in the table
    uint16_t flags;         // sample width, loop mode, metadata presence
};
```

### Flags

```
0x01  is_sample      a one-shot sample rather than a wavetable
0x02  loop_sample    that sample loops
0x04  int16          payload is int16 LE. CLEAR MEANS float32 LE
0x08  int16_is_16    int16 full scale is +/-32768 rather than +/-16384
0x10  has_metadata   a null-terminated <wtmeta> XML block follows the samples
```

**Bit 0x04 is the only thing that gives the sample width**, and it is the field
most easily got wrong: this word was previously read as a `data_offset` that
was "always 12". Bitwig writes flags `0x000C` (int16 + full scale), and 12
decimal is 0x000C, so on a Bitwig-only corpus the misreading is indistinguishable
from the truth. It falls apart the moment a Surge file appears, because Surge
writes float32 with flags `0x0000` and the payload is twice as wide.

`int16_is_16` changes decode scaling only, never the width. Without it a
full-scale sample decodes to roughly +/-16384 (15 bits, 6 dB of headroom).

---

## Sample Data

Immediately after the header: `frame_count * frame_samples` samples, laid out
**frame-major**. Frame 0's complete cycle comes first, then frame 1's, and so
on. There is no per-frame header and no interleave.

- Width: 4 bytes (float32 LE) unless flag `0x04` is set, then 2 (int16 LE).
- Per-frame byte size: `frame_samples * width`.
- To read frame `i`: seek to `12 + i * frame_samples * width`.

File size is exactly `12 + frame_count * frame_samples * width`, unless flag
`0x10` is set, in which case that value is where the XML trailer begins.

### Measured distribution

152 `vawt` files on hand:

| field | value | count |
|---|---|---|
| flags | `0x0000` (float32) | 151 |
| flags | `0x000C` (int16, full scale) | 1 |
| frame_samples | 2048 | 151 |
| frame_samples | 256 | 1 |

All 152 satisfy the flag-aware size formula exactly; none satisfies an
int16-only one. A prior revision of this document claimed a 5,636-file corpus
in which `data_offset` was always 12; that corpus is not available to re-check,
and the structural claim it supported is wrong, so the numbers above are the
ones this file now stands behind.

---

## acidcat inspect

`acidcat inspect FILE.wt` renders two regions, or three with a metadata trailer:

1. **vawt** , the 12-byte header, with `magic`, `frame_samples`, `frame_count`
   and `flags` decoded (the flag word is named, e.g. `int16+int16_is_16`), and
   a summary like `wavetable, 64 frame(s) x 2048 samples, 32-bit float`.
2. **samples** , the sample block, reported by count and depth.
3. **wtmeta** , present only when flag `0x10` is set.

The walker reads only the 12-byte header. It warns when the file is shorter
than the header implies, or when it is longer without the metadata flag set to
account for it.

---

## Notes

- The format is honest and self-describing: header plus a flat sample array, no
  compression, no length ambiguity. Contrast with Serum's `.SerumPreset`, where
  wavetable data is zlib-compressed 32-bit float.
- Frame-major layout means a single wave is contiguous, so slicing out one
  cycle is a plain seek + read; no de-interleave step.
- Little-endian throughout, including the samples, unlike AIFF's big-endian PCM.
- A round trip through a host that writes int16 is lossy if the source was
  float, and re-frames the audio to the device's frame size.
