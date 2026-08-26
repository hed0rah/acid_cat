"""One minimal, valid specimen per format, generated rather than gathered.

`make_corpus.py` solved this for WAV, and said why: the corpus it replaced was
"2,327 files that exist on exactly one machine". The same problem turned up
again from the other end. The local corpus covered 12 of 66 supported formats,
because nearly all of those 2,327 files were WAVs -- and the eight bugs that
widening it found were all in walkers that had never seen a specimen.

Gathering the rest does not fix that. Half the missing formats live inside game
discs, sample-library archives or software nobody has installed, and a corpus
assembled from one person's drives is unreproducible by construction: it is the
same defect wearing the other face.

So these are BUILT. Every byte is synthetic, deterministic and ours, which
makes them committable, tiny, and identical on every runner.

WHAT THIS IS AND IS NOT FOR. A generated specimen is a structurally valid file,
not a realistic one. It is enough to exercise a walker's parsing, its bounds
checks, and its behaviour under mutation -- which is what the robustness sweep
needs, since a mutation does not care whether the bytes came from a real
product. It is NOT enough to verify that acidcat reports musically correct
answers about real-world files, and nothing here should be read as covering
that. Real specimens still earn their place; they just cannot be the only
thing standing between a walker and its first hostile input.

Every specimen is checked on the way out: if `sniff` does not name it, the
build fails rather than writing a file that silently tests nothing.
"""

import os
import struct

_SEED = 0x5EED


def _noise(n, seed=_SEED):
    """Deterministic filler. Not random: a corpus that changes between runs
    turns a reproducible failure into a flake."""
    out = bytearray(n)
    x = seed
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out[i] = (x >> 16) & 0xFF
    return bytes(out)


def _chunk(cid, payload):
    return (cid + struct.pack("<I", len(payload)) + payload
            + (b"\x00" if len(payload) % 2 else b""))


# ── the specimens ───────────────────────────────────────────────────

def _dmx():
    """Doom DS* lump: u16 format=3, u16 rate, u32 count, then unsigned 8-bit.

    The count is bytes-after-the-header and must equal the lump length exactly
    -- that arithmetic IS the identification, since the format has no magic.
    """
    pcm = bytes((0x80 + (i % 40) - 20) & 0xFF for i in range(2000))
    return struct.pack("<HHI", 3, 11025, len(pcm)) + pcm


def _voc():
    """Creative Voice: 20-byte magic, u16 header size, u16 version, u16 check,
    then blocks. Block 01 carries a time constant; the terminator is ONE byte."""
    hdr = b"Creative Voice File\x1a" + struct.pack("<HHH", 0x1A, 0x010A, 0x1129)
    pcm = _noise(1500)
    body = struct.pack("<B", 1) + struct.pack("<I", len(pcm) + 2)[:3]
    body += bytes([256 - 1000000 // 11025 & 0xFF, 0]) + pcm
    return hdr + body + b"\x00"


def _aiff(compressed=False):
    """IFF/AIFF: FORM..AIFF with COMM and SSND. The sample rate is an 80-bit
    extended float, which is the part everyone gets wrong."""
    ext = b"\x40\x0e\xac\x44" + b"\x00" * 6          # 44100.0 as 80-bit
    if compressed:
        comm = struct.pack(">hIh", 1, 500, 16) + ext + b"NONE" + b"\x0enot compressed"
        form = b"AIFC"
        chunks = _iff(b"FVER", struct.pack(">I", 0xA2805140)) + _iff(b"COMM", comm)
    else:
        comm = struct.pack(">hIh", 1, 500, 16) + ext
        form = b"AIFF"
        chunks = _iff(b"COMM", comm)
    ssnd = struct.pack(">II", 0, 0) + _noise(1000)
    body = form + chunks + _iff(b"SSND", ssnd)
    return b"FORM" + struct.pack(">I", len(body)) + body


def _iff(cid, payload):
    return (cid + struct.pack(">I", len(payload)) + payload
            + (b"\x00" if len(payload) % 2 else b""))


def _rf64():
    """RF64: RIFF with a 64-bit size escape. riff_size is -1 and the real
    sizes live in ds64, which is the whole point of the format."""
    pcm = _noise(2000)
    fmt = struct.pack("<HHIIHH", 1, 2, 48000, 48000 * 4, 4, 16)
    ds64 = struct.pack("<QQQI", 0, len(pcm), len(pcm) // 4, 0)
    body = (b"WAVE" + _chunk(b"ds64", ds64) + _chunk(b"fmt ", fmt)
            + b"data" + struct.pack("<I", 0xFFFFFFFF) + pcm)
    return b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body


def _smus():
    """EA IFF SMUS: the 1985 music score form. FORM..SMUS with SHDR."""
    shdr = struct.pack(">HBB", 120, 1, 0)
    body = b"SMUS" + _iff(b"SHDR", shdr) + _iff(b"NAME", b"synthetic\x00")
    return b"FORM" + struct.pack(">I", len(body)) + body


def _8svx():
    """Amiga 8SVX: FORM..8SVX, VHDR then BODY of signed 8-bit."""
    vhdr = struct.pack(">IIIHBBI", 1000, 0, 32, 11025, 1, 0, 0x10000)
    body = b"8SVX" + _iff(b"VHDR", vhdr) + _iff(b"NAME", b"synthetic\x00") \
        + _iff(b"BODY", _noise(1000))
    return b"FORM" + struct.pack(">I", len(body)) + body


def _midi():
    """Standard MIDI File: MThd then one MTrk ending in the required
    end-of-track meta event."""
    thd = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96)
    ev = (b"\x00\x90\x3c\x40"          # note on
          b"\x60\x80\x3c\x40"          # note off
          b"\x00\xff\x2f\x00")         # end of track
    return thd + b"MTrk" + struct.pack(">I", len(ev)) + ev


def _sf2():
    """SoundFont 2: RIFF..sfbk with the three required LISTs. Minimal, but the
    chunk skeleton is what a walker reads."""
    ifil = _chunk(b"ifil", struct.pack("<HH", 2, 1))
    isng = _chunk(b"isng", b"EMU8000\x00")
    inam = _chunk(b"INAM", b"synthetic\x00")
    info = _chunk(b"LIST", b"INFO" + ifil + isng + inam)
    sdta = _chunk(b"LIST", b"sdta" + _chunk(b"smpl", _noise(2000)))
    pdta = _chunk(b"LIST", b"pdta"
                  + _chunk(b"phdr", b"\x00" * 38 * 2)
                  + _chunk(b"shdr", b"\x00" * 46 * 2))
    body = b"sfbk" + info + sdta + pdta
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _specimens():
    return [
        ("dmx.lmp", _dmx()),
        ("voc.voc", _voc()),
        ("aiff.aiff", _aiff()),
        ("aifc.aifc", _aiff(compressed=True)),
        ("rf64.wav", _rf64()),
        ("smus.iff", _smus()),
        ("8svx.iff", _8svx()),
        ("midi.mid", _midi()),
        ("sf2.sf2", _sf2()),
    ]


def build(outdir, verify=True):
    """Write every specimen, and refuse to write one acidcat cannot name.

    The check is the point. A generated file that no walker recognises adds a
    seed to the corpus and coverage to nothing, which is the failure this whole
    exercise exists to stop being invisible.
    """
    os.makedirs(outdir, exist_ok=True)
    written, unnamed = [], []
    for name, data in _specimens():
        path = os.path.join(outdir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        written.append(path)
    if verify:
        from acidcat.core.infra import sniff
        for path in written:
            try:
                kind = sniff.sniff(path)
            except Exception as exc:
                kind = "raised %s" % type(exc).__name__
            if not kind or str(kind).startswith("raised"):
                unnamed.append((os.path.basename(path), kind))
        if unnamed:
            raise AssertionError(
                "generated specimens acidcat could not name:\n  " + "\n  ".join(
                    "%s -> %r" % (n, k) for n, k in unnamed))
    return written


DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "format_corpus_generated")


def ensure(outdir=DEFAULT_DIR):
    """Build if missing; cheap enough to call on every run."""
    marker = os.path.join(outdir, "dmx.lmp")
    if not os.path.isfile(marker):
        build(outdir)
    return outdir


if __name__ == "__main__":
    import sys
    paths = build(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR)
    print("wrote %d specimens" % len(paths))
