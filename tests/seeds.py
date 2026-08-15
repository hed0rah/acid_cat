"""One place that can build a valid specimen of a format.

Constructing an input was reinvented 56 times across this suite -- 46 of those
independently assembling a `WAVE` + `fmt ` header -- and that is the reason the
differential fuzzer covers exactly one of 52 registered walkers. A fuzzer is
only as wide as its ability to produce a seed, so scattering the seed builders
put a ceiling on it that had nothing to do with the fuzzing.

Each seed is deliberately MINIMAL and deliberately VALID: minimal so a mutation
has a good chance of landing somewhere structural rather than in a field of
padding, and valid because a fuzzer that starts from garbage tests the reject
path over and over and never reaches the parser.

Every seed here is checked against the sniffer in test_walker_fuzz.py, so a
builder that drifts out of shape fails loudly instead of quietly seeding the
sweep with bytes that walk as something else.
"""

import struct

_SAME = object()
SEEDS = {}


def seed(fmt, ext, sniffs_as=_SAME):
    """Register a builder under a name, and say what the sniffer should call it.

    `sniffs_as=None` is meaningful rather than missing: the unknown-container
    seed is supposed to be unrecognised, because the walker it exercises is the
    one that runs when nothing recognises anything.
    """
    def deco(fn):
        SEEDS[fmt] = (fn, ext, fmt if sniffs_as is _SAME else sniffs_as)
        return fn
    return deco


def _riff_chunk(cid, payload):
    body = cid + struct.pack("<I", len(payload)) + payload
    return body + (b"\x00" if len(payload) % 2 else b"")


def _iff_chunk(cid, payload):
    body = cid + struct.pack(">I", len(payload)) + payload
    return body + (b"\x00" if len(payload) % 2 else b"")


@seed("wav", ".wav")
def wav(channels=1, bits=16, rate=44100, frames=64):
    align = channels * bits // 8
    pcm = b"\x00" * (frames * align)
    body = (b"WAVE"
            + _riff_chunk(b"fmt ", struct.pack("<HHIIHH", 1, channels, rate,
                                               rate * align, align, bits))
            + _riff_chunk(b"data", pcm))
    return b"RIFF" + struct.pack("<I", len(body)) + body


@seed("aiff", ".aiff")
def aiff(channels=1, frames=441, bits=16):
    # 80-bit IEEE extended for 44100, the way AIFF stores a sample rate
    rate = b"\x40\x0e\xac\x44\x00\x00\x00\x00\x00\x00"
    body = (b"AIFF"
            + _iff_chunk(b"COMM", struct.pack(">hIh", channels, frames, bits) + rate)
            + _iff_chunk(b"SSND", struct.pack(">II", 0, 0) + b"\x00" * 32))
    return b"FORM" + struct.pack(">I", len(body)) + body


@seed("8svx", ".iff")
def svx(frames=64):
    body = (b"8SVX"
            + _iff_chunk(b"VHDR", struct.pack(">IIIHBBI", frames, 0, 32,
                                              8000, 1, 0, 0x10000))
            + _iff_chunk(b"BODY", b"\x01" * frames))
    return b"FORM" + struct.pack(">I", len(body)) + body


@seed("midi", ".mid")
def midi(division=96):
    track = b"\x00\xff\x2f\x00"                      # end-of-track, and nothing else
    return (b"MThd" + struct.pack(">IHHH", 6, 0, 1, division)
            + b"MTrk" + struct.pack(">I", len(track)) + track)


@seed("flac", ".flac")
def flac(rate=44100, ch=2, bits=16, total=441):
    def blk(bt, payload, last=False):
        return (bytes([(0x80 if last else 0) | bt])
                + struct.pack(">I", len(payload))[1:] + payload)
    packed = (rate << 44) | ((ch - 1) << 41) | ((bits - 1) << 36) | total
    streaminfo = (struct.pack(">HH", 4096, 4096) + b"\x00\x00\x0e"
                  + b"\x00\x33\xa8" + struct.pack(">Q", packed) + b"\xab" * 16)
    return (b"fLaC" + blk(0, streaminfo)
            + blk(1, b"\x00" * 16, last=True)        # PADDING
            + b"\xff\xf8" + b"\x00" * 64)            # a frame-ish tail


@seed("ogg", ".ogg")
def ogg(serial=101, pages=3):
    def page(seq, *, bos=False, eos=False, body=b"\x11" * 512):
        htype = (0x02 if bos else 0) | (0x04 if eos else 0)
        segs, rest = [], len(body)
        while rest >= 255:
            segs.append(255)
            rest -= 255
        segs.append(rest)
        return (b"OggS" + bytes([0, htype]) + struct.pack("<q", seq * 1000)
                + struct.pack("<I", serial) + struct.pack("<I", seq)
                + struct.pack("<I", 0) + bytes([len(segs)]) + bytes(segs) + body)
    return b"".join(page(i, bos=(i == 0), eos=(i == pages - 1))
                    for i in range(pages))


@seed("unknown-container", ".bin", sniffs_as=None)
def unknown_container(magic=b"ZZZZ", n=4):
    """The shape `triage.generic_walk` claims: a tiling [tag][size] grid.

    Here because the walker that stands in for every format nobody has written
    a walker for deserves fuzzing more than most, not less -- it is the one
    that meets genuinely unknown bytes.
    """
    body = b"".join(b"ch%02d" % i + struct.pack(">I", 32) + b"\xaa" * 32
                    for i in range(n))
    return magic + struct.pack(">I", len(body)) + body


def build(fmt):
    """Bytes for one registered format."""
    return SEEDS[fmt][0]()


def suffix(fmt):
    """The extension to give a temp file, since some walkers sniff on it."""
    return SEEDS[fmt][1]


def sniffs_as(fmt):
    """What `sniff` should return for this seed, or None if nothing should."""
    return SEEDS[fmt][2]
