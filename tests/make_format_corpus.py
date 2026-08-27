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

Every specimen is checked on the way out, three times, because "valid",
"reached" and "used" are three different claims and only the first is obvious:

  named   -- `sniff` must identify it, or no walker is ever selected for it
  walked  -- `walk_file` must parse it, not merely refuse it; a specimen that
             sniffs and then bounces exercises the refusal path and nothing past
  swept   -- it must exceed SEED_FLOOR, or the mutation sweep skips it and the
             specimen is built, verified, and then never mutated

Two specimens shipped failing the third check before it existed, which is the
whole argument for having it: each one read as coverage and was not.
"""

import os
import struct
import sys

_SEED = 0x5EED
_HERE = os.path.dirname(os.path.abspath(__file__))

# The mutation sweep ignores any seed at or under this size. Defined HERE and
# imported there, so a specimen cannot quietly fall under a floor the
# generator does not know about -- which is how two of them shipped unswept.
SEED_FLOOR = 64


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
    """EA IFF SMUS: the 1985 music score form. FORM..SMUS with SHDR, then one
    TRAK of note events.

    The TRAK is what makes this a score rather than a header. It is also what
    lifts the file over the sweep's 64-byte seed floor -- at 42 bytes the
    header-only version was skipped by every mutation run.
    """
    shdr = struct.pack(">HBB", 120, 1, 0)
    # sID 0x00 = note: [pitch][chan/vol][duration]; 0x81 = end of track
    trak = b"".join(bytes([n, 0x40, 0x10]) for n in range(60, 72)) + b"\x81\x00\x00"
    body = (b"SMUS" + _iff(b"SHDR", shdr) + _iff(b"NAME", b"synthetic\x00")
            + _iff(b"TRAK", trak))
    return b"FORM" + struct.pack(">I", len(body)) + body


def _8svx():
    """Amiga 8SVX: FORM..8SVX, VHDR then BODY of signed 8-bit."""
    vhdr = struct.pack(">IIIHBBI", 1000, 0, 32, 11025, 1, 0, 0x10000)
    body = b"8SVX" + _iff(b"VHDR", vhdr) + _iff(b"NAME", b"synthetic\x00") \
        + _iff(b"BODY", _noise(1000))
    return b"FORM" + struct.pack(">I", len(body)) + body


def _midi():
    """Standard MIDI File: MThd then one MTrk ending in the required
    end-of-track meta event.

    Carries a track name, a tempo and a scale rather than one note, because
    the sweep skips any seed under 64 bytes -- the first version of this was
    34 and was built, verified, and then silently never mutated.
    """
    thd = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96)
    name = b"\x00\xff\x03\x09synthetic"          # track name meta
    tempo = b"\x00\xff\x51\x03\x07\xa1\x20"      # 500000 us/qn = 120 bpm
    notes = b"".join(b"\x00\x90" + bytes([n, 0x40]) + b"\x30\x80" + bytes([n, 0x40])
                     for n in range(60, 72))
    ev = name + tempo + notes + b"\x00\xff\x2f\x00"
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


def _rmid():
    """RIFF-wrapped MIDI: RIFF..RMID with the whole SMF inside one data chunk.

    Windows' container for a MIDI file, and the reason a RIFF reader has to
    check the form type rather than assume WAVE.
    """
    return b"RIFF" + struct.pack("<I", 4 + 8 + len(_midi())) + b"RMID" \
        + _chunk(b"data", _midi())


def _ableton_xml(child, version="11.0_11300"):
    """One gzipped Ableton XML document.

    Every Ableton document except .asd and .amxd is this: gzip around an XML
    file whose root is <Ableton> and whose FIRST CHILD names the type. There
    is no magic beyond gzip's own, so the child element is the identification
    -- which is why the specimens differ only in that one tag.
    """
    import gzip
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Ableton MajorVersion="5" MinorVersion="%s" Creator="Ableton Live 11" '
        'Revision="synthetic">\n'
        '\t<%s>\n'
        '\t\t<Name Value="synthetic" />\n'
        '\t\t<Annotation Value="" />\n'
        '\t</%s>\n'
        '</Ableton>\n' % (version, child, child)
    ).encode("utf-8")
    # mtime pinned: gzip stamps the current time by default, which would make
    # the corpus differ between runs and turn a reproducible failure into a
    # flake.
    return gzip.compress(body, mtime=0)


# ── specimens borrowed from the suite's own fixtures ────────────────
#
# Sixteen more formats already had a builder: the walker tests construct a
# minimal valid file to have something to assert against. Writing a second
# copy here would be writing a second definition of each format, and the two
# would drift -- so these call the test's builder and keep one source of truth
# per format.
#
# The tradeoff is worth naming. A builder encodes its author's understanding
# of the format, so if it is wrong, the corpus inherits that error and the
# walker is measured against its own assumption. That is not hypothetical:
# three tests in this repo were found asserting the bug they were meant to
# catch. It still beats the alternative, because a mutation does not care
# whether the unmutated bytes were realistic -- it only needs a starting point
# the walker will agree to parse. Correctness of the *format* is what real
# specimens are for; these buy coverage of the *parser*.

def _borrowed():
    """Build one specimen per format that already has a fixture builder.

    Returns [] rather than raising if the test modules cannot be imported, so
    that generating the corpus outside a test run still produces the
    hand-written specimens instead of nothing.
    """
    import pathlib
    import shutil
    import tempfile

    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    try:
        import test_ableton
        import test_akai
        import test_albank
        import test_bfdlac
        import test_emu
        import test_krz
        import test_mpc
        import test_ncw
        import test_s3m
        import test_tracker
    except ImportError:
        return []

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="acidcat-corpus-"))
    try:
        out = [
            ("albank.ctl", test_albank._make_ctl()),
            ("bfdlac.bfdlac", test_bfdlac._bfdc(
                [test_bfdlac._fmt(), test_bfdlac._indx()])),
            ("krz.krz", test_krz._bank(
                [test_krz._object(1, 1, "Samp", test_krz._sample_body())],
                pcm=b"\x00\x00" * 100)),
            ("ncw.ncw", test_ncw.make_ncw(1, 16, 44100, [[0] * 512], bits=8)),
            ("mod.mod", test_tracker._make_mod()),
            ("xm.xm", test_tracker._make_xm()),
            # returns (blob, offset, offset); only the bytes matter here
            ("it.it", test_tracker._make_it()[0]),
            ("s3m.s3m", test_s3m._make_s3m()),
            ("asd.asd", test_ableton.build_asd(
                test_ableton.grid_for(44100, 2.0))),
        ]
        # these three write a file and hand back its path
        for name, made in (("akai.akp", test_akai._make_akp(tmp)),
                           ("e4b.e4b", test_emu._make_e4b(tmp)),
                           ("e5b.exb", test_emu._make_e5b(tmp)),
                           ("xpn.xpn", test_mpc._make_xpn(tmp)),
                           ("xtd.xtd", test_mpc._make_xtd(tmp)),
                           ("snd.snd", test_mpc._make_snd(tmp)),
                           # the MPC1000 variant; the MPC2000 builder makes a
                           # 36-byte file, under the sweep floor, and this
                           # already covers the format
                           ("pgm.pgm", test_mpc._make_pgm_mpc1000(
                               tmp, ["Kick", "Snare"]))):
            with open(str(made), "rb") as fh:
                out.append((name, fh.read()))
        return out
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def _specimens():
    return _borrowed() + [
        ("dmx.lmp", _dmx()),
        ("voc.voc", _voc()),
        ("aiff.aiff", _aiff()),
        ("aifc.aifc", _aiff(compressed=True)),
        ("rf64.wav", _rf64()),
        ("smus.iff", _smus()),
        ("8svx.iff", _8svx()),
        ("midi.mid", _midi()),
        ("sf2.sf2", _sf2()),
        ("rmid.rmi", _rmid()),
        # the gzipped-XML family: identical but for the root's first child
        ("adg.adg", _ableton_xml("GroupDevicePreset")),
        ("agr.agr", _ableton_xml("Groove")),
        ("als.als", _ableton_xml("LiveSet")),
        # same document as als; the .alc EXTENSION is what splits them, which
        # is worth a specimen precisely because it is the fragile half
        ("alc.alc", _ableton_xml("LiveSet")),
        # any root child the map does not know falls through to adv
        ("adv.adv", _ableton_xml("DeviceChainPreset")),
    ]


def build(outdir, verify=True):
    """Write every specimen, and refuse to write one acidcat cannot name.

    The check is the point. A generated file that no walker recognises adds a
    seed to the corpus and coverage to nothing, which is the failure this whole
    exercise exists to stop being invisible.
    """
    os.makedirs(outdir, exist_ok=True)
    written, unnamed, unswept = [], [], []
    for name, data in _specimens():
        path = os.path.join(outdir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        written.append(path)
        # Being named is not the same as being USED. The mutation sweep skips
        # any seed at or under SEED_FLOOR bytes, so a specimen under it is
        # built, verified, and then never mutated -- coverage that reads as
        # present and is not. Two of these shipped that way.
        if len(data) <= SEED_FLOOR:
            unswept.append((name, len(data)))
    if unswept:
        raise AssertionError(
            "specimens at or under the %d-byte sweep floor, so nothing will "
            "mutate them:\n  " % SEED_FLOOR + "\n  ".join(
                "%s (%d B)" % (n, s) for n, s in unswept))
    if verify:
        from acidcat.core.infra import sniff
        from acidcat.core.walk import Unsupported, walk_file
        unwalked = []
        for path in written:
            try:
                kind = sniff.sniff(path)
            except Exception as exc:
                kind = "raised %s" % type(exc).__name__
            if not kind or str(kind).startswith("raised"):
                unnamed.append((os.path.basename(path), kind))
                continue
            # Being NAMED is still not the same as being parsed. A specimen
            # that sniffs and then bounces off its walker exercises the
            # refusal path and nothing past it, which is not what a
            # per-format corpus is for.
            try:
                walk_file(path)
            except Unsupported as exc:
                unwalked.append((os.path.basename(path), kind, str(exc)))
            except Exception as exc:
                unwalked.append((os.path.basename(path), kind,
                                 "%s: %s" % (type(exc).__name__, exc)))
        if unnamed:
            raise AssertionError(
                "generated specimens acidcat could not name:\n  " + "\n  ".join(
                    "%s -> %r" % (n, k) for n, k in unnamed))
        if unwalked:
            raise AssertionError(
                "generated specimens that sniff but do not walk:\n  " + "\n  ".join(
                    "%s (%s) -> %s" % (n, k, e) for n, k, e in unwalked))
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
