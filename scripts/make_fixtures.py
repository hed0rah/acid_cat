"""Generate the small tracked fixture set in data/fixtures/.

data/test_formats/ is gitignored and 16 MB, so a fresh clone skipped every test
that needs a real encoded file: the TUI suites, the tagging round-trips, strip,
resync, locate and od. These are the same formats at a size worth committing.

They match the corpus files they stand in for -- 44.1 kHz, 16-bit, stereo, and
a WAV carrying a LIST chunk as well as fmt and data -- because tests assert on
those properties, and a stand-in that differs turns a skip into a failure.

They are REAL encoder output, not hand-built headers, except the WAV, which is
written here so it can carry the extra chunk. A synthetic specimen only proves
the parser agrees with whoever wrote the specimen, which is how the .wt walker
stayed wrong through an entire corpus.

    python scripts/make_fixtures.py        # needs ffmpeg on PATH

Committed output; rerun only when a fixture needs to change.
"""
import math
import pathlib
import struct
import subprocess
import sys

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "fixtures"
RATE, CHANNELS, SECONDS = 44100, 2, 0.25


def pcm():
    n = int(RATE * SECONDS)
    buf = bytearray()
    for i in range(n):
        # two partials, so the signal is not a pure sine -- a pure tone
        # compresses to nearly nothing and makes a degenerate test file
        v = 0.42 * math.sin(2 * math.pi * 440 * i / RATE) + \
            0.18 * math.sin(2 * math.pi * 660 * i / RATE)
        fade = min(1.0, i / 512, (n - i) / 512)          # no click at the edges
        s = int(max(-1.0, min(1.0, v * fade)) * 30000)
        buf += struct.pack("<hh", s, s)
    return bytes(buf)


def wav(path):
    """A WAV with fmt, LIST and data, matching the corpus file's shape."""
    audio = pcm()
    block = CHANNELS * 2
    fmt = struct.pack("<HHIIHH", 1, CHANNELS, RATE, RATE * block, block, 16)
    info = b"INFO" + b"ISFT" + struct.pack("<I", 14) + b"acidcat tests\x00"
    body = (b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"LIST" + struct.pack("<I", len(info)) + info
            + b"data" + struct.pack("<I", len(audio)) + audio)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def wav24(path):
    """A TRUE 24-bit WAV, written here rather than upconverted.

    Converting the 16-bit tone with ffmpeg produced a file whose low eight bits
    are always zero, and acidcat says so: "declared 24-bit, effective 16-bit --
    likely upsampled from 16-bit (padded, not true 24-bit)". That is the tool
    being right, and it makes the fixture a poor stand-in for real 24-bit audio.
    Full-depth samples, so the bottom byte carries signal.
    """
    n = int(RATE * SECONDS)
    buf = bytearray()
    for i in range(n):
        v = 0.42 * math.sin(2 * math.pi * 440 * i / RATE) + \
            0.18 * math.sin(2 * math.pi * 660 * i / RATE)
        fade = min(1.0, i / 512, (n - i) / 512)
        s = int(max(-1.0, min(1.0, v * fade)) * 8_000_000)
        three = struct.pack("<i", s)[:3]           # low 3 bytes, little-endian
        buf += three * CHANNELS
    block = CHANNELS * 3
    fmt = struct.pack("<HHIIHH", 1, CHANNELS, RATE, RATE * block, block, 24)
    body = (b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(buf)) + bytes(buf))
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


ENCODES = [
    ("tone.mp3", ["-c:a", "libmp3lame", "-b:a", "96k"]),
    ("tone.flac", ["-c:a", "flac", "-compression_level", "8"]),
    ("tone.ogg", ["-c:a", "libvorbis", "-q:a", "2"]),
    ("tone.opus", ["-c:a", "libopus", "-b:a", "48k"]),
    ("tone.m4a", ["-c:a", "aac", "-b:a", "96k"]),

    # Structurally distinct, not just differently encoded. Each one is the only
    # committed file exercising a header path, so a stand-in of the wrong shape
    # would turn a skip into a failure rather than into coverage:
    #
    #   tone24     16-bit is the common case; 24-bit is a different sample width
    #              through every reader that computes a frame size
    #   tone51     over two channels, WAV switches to WAVE_FORMAT_EXTENSIBLE:
    #              a different fmt chunk with a channel mask and a sub-format
    #              GUID, which is its own parse path
    #   tone.aiff  big-endian, and a COMM/SSND container rather than RIFF
    #   tone24     for FLAC, bit depth lives in the STREAMINFO bitfield
    ("tone51.wav", ["-c:a", "pcm_s16le", "-ac", "6"]),
    ("tone.aiff", ["-c:a", "pcm_s16be"]),
    ("tone24.flac", ["-c:a", "flac", "-sample_fmt", "s32"]),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    src = OUT / "tone.wav"
    wav(src)
    print(f"  {src.name:12} {src.stat().st_size:>7,} bytes")

    deep = OUT / "tone24.wav"
    wav24(deep)
    print(f"  {deep.name:12} {deep.stat().st_size:>7,} bytes")

    for name, args in ENCODES:
        dst = OUT / name
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                            *args, str(dst)], capture_output=True, text=True)
        if r.returncode:
            print(f"  {name}: ffmpeg failed -- {r.stderr.strip()[:120]}")
            return 1
        print(f"  {name:12} {dst.stat().st_size:>7,} bytes")

    files = sorted(OUT.iterdir())
    print(f"\n  {len(files)} files, {sum(p.stat().st_size for p in files):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
