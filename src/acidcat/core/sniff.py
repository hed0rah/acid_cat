"""Canonical format detection by magic bytes.

One sniffing routine shared by the format walkers (and available to any
command), so the per-verb magic tables cannot drift apart. ``sniff_bytes``
classifies a 16-byte head; ``sniff`` reads the head from disk and also
resolves the one ambiguous case, an ID3v2 tag that wraps a non-MP3
container (some tools prepend ID3 tags to WAV/AIFF/FLAC files).

The check order is part of the contract: RIFF/WAVE must be tried before
the RIFF/NIKS preset magic, and the MP4 ftyp probe before the ID3
fallbacks, or edge-case files reroute. Do not reorder.

Every id this module can return is declared in ``KNOWN_FORMATS`` below -- the
canonical format-id namespace the rest of acidcat keys its dispatch tables on
(walk/_WALKERS, samples extractors, convert/repair). ``sniff`` returns one of
those ids or None. A test guards that the tables only use ids from this set (so a
typo'd key fails loudly, not as a silent dict-miss) and that the set stays in
sync with the ids sniff actually returns.

MOD has no leading signature (its magic is at offset 1080), so ``sniff``
confirms it from disk; ``sniff_bytes`` cannot classify a MOD from a head.
"""

from acidcat.core import mp3 as mp3mod
from acidcat.core.codecs import ncw as ncwmod

# containers an ID3v2 tag is known to wrap; the tag then does not make
# the file an MP3.
_ID3_WRAPPED_MAGICS = (b"RIFF", b"RF64", b"FORM", b"fLaC", b"MThd")

# the canonical set of ids sniff/sniff_bytes can return. This is the source of
# truth every dispatch table keys on; keep it in sync with the returns below (the
# test suite asserts both directions). "id3-wrapped" is a sentinel, not a format.
KNOWN_FORMATS = frozenset({
    "8svx", "adx", "aifc", "aiff", "akp", "albank", "bfdlac", "bitwig", "brstm",
    "cdxa", "cue", "e4b", "e5b", "fc", "flac", "fxp", "gcm", "gf1pat", "hps",
    "id3-wrapped", "iq", "it", "krz", "labx", "med", "midi", "midi2", "mod",
    "mp3", "mp4", "mpcpattern", "multisample", "n64rom", "ncw", "ni", "ogg",
    "okt", "pgm", "rf64", "rmid", "rx2", "s3m", "serum", "sf2", "sigmf", "smus",
    "snd", "snesrom", "vag", "vital", "wav", "wii", "wt", "xm", "xpm", "xpn", "xtd",
})

# audio container formats that carry a carvable/recoverable payload: format id ->
# (leading magic to sweep for, natural file extension for a carved region). The one
# definition `locate` (which magics to scan and which sniffed formats to accept)
# and `carve` (how to name a carved region) both read, so the two cannot drift.
# A hit is always re-confirmed with sniff_bytes, so the magic here is a coarse
# scan pattern, not the identification (RIFF and ID3 each cover several formats).
AUDIO_CONTAINERS = {
    "wav":  (b"RIFF", "wav"),
    "rf64": (b"RF64", "wav"),
    "aiff": (b"FORM", "aiff"),
    "aifc": (b"FORM", "aiff"),
    "8svx": (b"FORM", "8svx"),
    "flac": (b"fLaC", "flac"),
    "ogg":  (b"OggS", "ogg"),
    "sf2":  (b"RIFF", "sf2"),
    "mp3":  (b"ID3",  "mp3"),
}

# distinct leading magics of the audio containers, in first-seen order (the scan
# patterns for locate's signature sweep); and the id set locate accepts.
AUDIO_CONTAINER_MAGICS = tuple(dict.fromkeys(m for m, _ext in AUDIO_CONTAINERS.values()))
AUDIO_CONTAINER_FMTS = frozenset(AUDIO_CONTAINERS)
AUDIO_CONTAINER_EXT = {fid: ext for fid, (_m, ext) in AUDIO_CONTAINERS.items()}


def sniff_bytes(head):
    """Classify the first bytes of a file (pass at least 16).

    Magic-only: an ID3v2 tag classifies as "mp3" here; use ``sniff`` to
    distinguish a tag that wraps a different container.
    """
    if head[:12] == b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00":
        return "cdxa"                                   # raw CD sector image (Mode1/2/2352)
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"sfbk":
        return "sf2"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"RMID":
        return "rmid"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"APRG":
        return "akp"                                   # Akai S5000/S6000 program
    if head[4:15] == b"MPC1000 PGM":                   # Akai MPC1000/2500 program
        return "pgm"
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff" if head[8:12] == b"AIFF" else "aifc"
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] == b"E4B0":
        return "e4b"                                   # E-MU Emulator 4 / EOS bank
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] == b"E5B0":
        return "e5b"                                   # E-MU Emulator X / Proteus X
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] == b"8SVX":
        return "8svx"                                  # IFF 8-bit sampled voice (Amiga)
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] == b"SMUS":
        return "smus"                                  # IFF Sonix musical score (Amiga)
    if head[:8] == b"OKTASONG":
        return "okt"                                   # Oktalyzer module (Amiga)
    if head[:4] in (b"MMD0", b"MMD1", b"MMD2", b"MMD3"):
        return "med"                                   # MED / OctaMED module (Amiga)
    if head[:4] in (b"SMOD", b"FC14"):
        return "fc"                                    # Future Composer chiptune (Amiga)
    if head[:4] == b"PRAM" or head[:4] == b"SROM":
        return "krz"                                   # Kurzweil K2000/K2500/K2600
    if head[:4] == b"BFDC":
        return "bfdlac"                                # FXpansion BFD compressed audio
    if head[:8] == b"GF1PATCH":
        return "gf1pat"                                # Gravis UltraSound GF1 patch
    if head[:4] == b"VAGp":
        return "vag"                                   # PS1 SPU-ADPCM sample
    if head[:8] == b" HALPST\x00":
        return "hps"                                   # HAL PCM Stream (GameCube DSP-ADPCM)
    if head[:4] == b"RSTM":
        return "brstm"                                 # Nintendo streamed audio (GameCube/Wii DSP-ADPCM)
    if head[:6] == b'FILE "':
        return "cue"                                   # CUE sheet (CD-DA track layout)
    if head[:8] == b"SMF2CLIP":
        return "midi2"                                 # MIDI 2.0 clip file (a UMP stream)
    if len(head) >= 14 and head[:4] == b"MThd":
        return "midi"
    if len(head) >= 12 and head[:4] == b"RF64" and head[8:12] == b"WAVE":
        return "rf64"
    if head[:8] == b"XferJson":
        return "serum"
    if head[:4] == b"vawt":
        return "wt"
    if head[:4] == b"BtWg":
        return "bitwig"
    if head[:4] == b"CcnK":
        return "fxp"
    if head[:4] == b"CAT " and head[8:12] == b"REX2":
        return "rx2"
    if head[:4] == ncwmod.MAGIC:
        return "ncw"
    if head[:1] == b"{":
        return "vital"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[12:16] == b"hsin" or head[:4] == b"-in-" \
            or (head[:4] == b"RIFF" and head[8:12] == b"NIKS"):
        return "ni"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:17] == b"Extended Module: ":
        return "xm"
    if head[:4] == b"IMPM":
        return "it"
    if head[:3] == b"ID3" or (len(head) >= 4
                              and mp3mod.decode_frame_header(head[:4]) is not None):
        return "mp3"
    return None


def _id3_wraps_other_container(filepath):
    """True when the leading ID3v2 tag is a wrapper around a different
    known container rather than the tag of an MPEG stream."""
    hdr = mp3mod.read_id3v2(filepath)
    if not hdr:
        return False  # "ID3" magic but an unreadable header; treat as an MP3 attempt
    with open(filepath, "rb") as f:
        f.seek(hdr["total"])
        nxt = f.read(4)
    return nxt in _ID3_WRAPPED_MAGICS


def sniff(filepath):
    """Sniff a file on disk. Same ids as ``sniff_bytes`` plus
    "id3-wrapped" for an ID3v2 tag around a non-MP3 container."""
    with open(filepath, "rb") as f:
        head = f.read(20)  # 20 covers the 17-byte "Extended Module: " XM signature
    fmt = sniff_bytes(head)
    if fmt == "mp3" and head[:3] == b"ID3" and _id3_wraps_other_container(filepath):
        return "id3-wrapped"
    # a .cue may open with REM/CATALOG lines before FILE; trust the extension
    if fmt is None and filepath.lower().endswith(".cue"):
        return "cue"
    # ADX opens with 0x8000 (weak); confirm via the (c)CRI marker before the audio
    if fmt is None and head[:2] == b"\x80\x00":
        from acidcat.core.codecs import adx
        if adx.is_adx(filepath):
            return "adx"
    # disc images carry their magic past the sniff head: Wii at 0x18, GameCube at
    # 0x1C. Wii is checked first (its partitions are encrypted; distinct magic).
    if fmt is None:
        from acidcat.core import wiidisc
        if wiidisc.is_wii(filepath):
            return "wii"
    if fmt is None:
        from acidcat.core import gcm
        if gcm.is_gcm(filepath):
            return "gcm"
    # an N64 .ctl audio bank opens with the weak 2-byte 0x4231 ('B1') revision;
    # confirm structurally (a valid bank offset -> a bank with a sane sample rate)
    if fmt is None and _is_albank(filepath):
        return "albank"
    # an N64 ROM (z64/n64/v64) by its fixed magic word -- extract recovers its
    # VADPCM samples regardless of the game's bank format
    if fmt is None and head[:4] in (b"\x80\x37\x12\x40", b"\x37\x80\x40\x12", b"\x40\x12\x37\x80"):
        return "n64rom"
    # a SNES ROM has no leading magic; its internal cartridge header (LoROM 0x7FC0
    # / HiROM 0xFFC0) carries a checksum + complement that xor to 0xFFFF. extract
    # recovers its BRR samples regardless of the game's sample table.
    if fmt is None and _is_snes_rom(filepath):
        return "snesrom"
    # a .sigmf-meta is JSON starting with '{', which sniff_bytes reads as vital;
    # the mandated extension reroutes it, exactly like the id3-wrapped demotion.
    if fmt == "vital" and filepath.lower().endswith(".sigmf-meta"):
        return "sigmf"
    # an MPC .mpcpattern is also bare JSON ('{'); reroute on its extension.
    if fmt == "vital" and filepath.lower().endswith(".mpcpattern"):
        return "mpcpattern"
    # a ZIP whose archive holds multisample.xml is a Bitwig .multisample. This is
    # the one content-sniff that must peek inside the container (the local-file
    # header magic alone cannot tell it from any other zip).
    if fmt is None and head[:4] == b"PK\x03\x04" and _is_multisample(filepath):
        return "multisample"
    # an Arturia Analog Lab .labx is also a zip; its entries follow an
    # <Engine>/User|Factory/<Bank>/<Preset> layout of boost text archives. The
    # multisample check (an exact member name) is more specific, so it runs first.
    if fmt is None and head[:4] == b"PK\x03\x04" \
            and (filepath.lower().endswith(".labx") or _is_labx(filepath)):
        return "labx"
    # an Akai MPC .xpn expansion package is a zip carrying an Expansion.xml
    # manifest alongside its .xpm programs and samples.
    if fmt is None and head[:4] == b"PK\x03\x04" \
            and (filepath.lower().endswith(".xpn") or _is_xpn(filepath)):
        return "xpn"
    # an MPC3 .xtd track/kit is gzip wrapping an ACVS container; confirm the
    # ACVS magic inside rather than claiming every .xtd gzip.
    if fmt is None and head[:2] == b"\x1f\x8b" \
            and filepath.lower().endswith(".xtd") and _is_xtd(filepath):
        return "xtd"
    # a free-format MPEG sync (bitrate index 0): sniff_bytes stays strict
    # because 16 bytes cannot confirm it; with the file in hand, accept only
    # when the constant frame length is measurable (a matching second sync).
    if fmt is None and len(head) >= 4 and _free_format_mp3(filepath, head):
        return "mp3"
    # S3M's 'SCRM' magic sits at 0x2C (outside the head), a disk-level confirm.
    # It runs before the MOD check: it is cheaper and more precise, and MOD's
    # offset-1080 heuristic can false-positive inside S3M pattern data.
    if fmt is None and _is_s3m(filepath):
        return "s3m"
    # SigMF pair members and bare IQ captures are headerless: accept them only
    # when no magic matched, keyed on the mandated / conventional extensions.
    if fmt is None:
        low = filepath.lower()
        if low.endswith(".sigmf-data") or low.endswith(".sigmf-meta"):
            return "sigmf"
        if low.endswith(_IQ_EXTS) or (low.endswith(".raw") and _gqrx_sniff(filepath)):
            return "iq"
        # an MPC .xpm program is XML; content-confirm to avoid the X11 pixmap
        # that shares the extension.
        if low.endswith(".xpm") and _is_mpc_program(filepath):
            return "xpm"
        # an older MPC2000 .pgm has no magic (a 17-byte sample-name-table record
        # at offset 2); the MPC1000 form is caught by magic in sniff_bytes.
        if low.endswith(".pgm") and _is_mpc2000_pgm(filepath):
            return "pgm"
        # an MPC2000 .snd sound starts 0x01 0x02 then a printable name, which
        # distinguishes it from a NeXT/Sun .snd (magic ".snd").
        if low.endswith(".snd") and _is_mpc_snd(filepath):
            return "snd"
    # ProTracker MOD has no leading signature; its only reliable magic sits at
    # offset 1080, so it can only be confirmed with the file in hand.
    if fmt is None and _is_mod(filepath):
        return "mod"
    return fmt


def _is_albank(filepath):
    """An N64 libultra .ctl: revision 0x4231, a small bankCount, and a first bank
    offset that lands on an ALBank whose sampleRate is a sane audio rate. The
    sample-rate gate cheaply rejects the many false 0x4231 hits in random data."""
    import struct
    try:
        with open(filepath, "rb") as f:
            head = f.read(65536)
    except OSError:
        return False
    if len(head) < 16 or head[:2] != b"\x42\x31":
        return False
    bank_count = struct.unpack_from(">h", head, 2)[0]
    if not (1 <= bank_count <= 64) or 4 + 4 * bank_count > len(head):
        return False
    b0 = struct.unpack_from(">I", head, 4)[0]
    if not (4 + 4 * bank_count <= b0 <= len(head) - 8):
        return False
    sample_rate = struct.unpack_from(">i", head, b0 + 4)[0]
    return 8000 <= sample_rate <= 48000


def _is_snes_rom(filepath):
    """A headerless SNES ROM. No leading magic, but the internal cartridge header
    at 0x7FC0 (LoROM) or 0xFFC0 (HiROM) ends with a 16-bit checksum and its
    complement that xor to 0xFFFF -- a 1-in-65536 gate. A 512-byte copier header
    shifts both locations by 0x200. The map-mode byte (0x20..0x3F) is a sanity
    check so a chance complement pair in non-ROM data is not mistaken for a cart."""
    import os
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return False
    if size < 0x8000 or size > 0x800000:               # 32 KiB .. 8 MiB (SNES range)
        return False
    base = 0x200 if size % 0x400 == 0x200 else 0        # strip a 512-byte copier header
    with open(filepath, "rb") as f:
        for hdr in (0x7FC0, 0xFFC0):                    # LoROM, HiROM header locations
            f.seek(base + hdr)
            h = f.read(0x20)
            if len(h) < 0x20:
                continue
            mapmode = h[0x15]
            comp = h[0x1C] | (h[0x1D] << 8)
            chk = h[0x1E] | (h[0x1F] << 8)
            if chk and (comp ^ chk) == 0xFFFF and 0x20 <= mapmode <= 0x3F:
                return True
    return False


def _is_mod(filepath):
    from acidcat.core import tracker as tkmod
    try:
        with open(filepath, "rb") as f:
            return tkmod.is_mod(f.read(1084))
    except OSError:
        return False


def _is_s3m(filepath):
    from acidcat.core import tracker as tkmod
    try:
        with open(filepath, "rb") as f:
            return tkmod.is_s3m(f.read(48))
    except OSError:
        return False


# bare raw-IQ extensions (headerless): geometry comes from the extension itself.
_IQ_EXTS = (".cu8", ".c16", ".c8", ".cs8", ".cs16", ".cf32", ".cfile")


def _gqrx_sniff(filepath):
    from acidcat.core.walk import sigmf
    return sigmf._gqrx_name(filepath) is not None


def _is_mpc_program(filepath):
    """An MPC .xpm is XML with an <MPCVObject> root; distinguishes it from an
    X11 pixmap, which also uses .xpm."""
    try:
        with open(filepath, "rb") as f:
            return b"<MPCVObject" in f.read(512)
    except OSError:
        return False


def _is_mpc2000_pgm(filepath):
    """An MPC2000/2000XL .pgm: a 17-byte sample-name record at offset 2 (a
    printable name then a 0 at [18]). The MPC1000 form is caught by magic."""
    try:
        with open(filepath, "rb") as f:
            h = f.read(20)
    except OSError:
        return False
    return len(h) >= 19 and h[18] == 0 and 0x20 <= h[2] < 0x7f


def _is_mpc_snd(filepath):
    """An MPC2000 .snd sound: validity byte 1, a type byte < 5 (classic files
    use 4, some exporters 2), then a printable name -- not a NeXT/Sun .snd
    (which starts with the ASCII magic '.snd')."""
    try:
        with open(filepath, "rb") as f:
            h = f.read(3)
    except OSError:
        return False
    return len(h) >= 3 and h[0] == 1 and h[1] < 5 and 0x20 <= h[2] < 0x7f


def _free_format_mp3(filepath, head):
    hdr = mp3mod.decode_frame_header(head[:4], allow_free=True)
    if hdr is None or not hdr.get("free_format"):
        return False
    import os
    end = min(os.path.getsize(filepath), 2 * mp3mod._FREE_SCAN_CAP)
    with open(filepath, "rb") as f:
        return mp3mod._free_frame_length(f, 0, hdr, end) is not None


def _is_multisample(filepath):
    try:
        import zipfile
        with zipfile.ZipFile(filepath) as z:
            return "multisample.xml" in z.namelist()
    except Exception:
        return False


def _is_labx(filepath):
    """A zip whose entries follow <Engine>/User|Factory/<Bank>/<Preset> and hold
    boost text-serialization archives (Arturia Analog Lab bank export)."""
    try:
        import zipfile
        with zipfile.ZipFile(filepath) as z:
            for n in z.namelist()[:8]:
                if len(n.split("/")) >= 3 and ("/User/" in n or "/Factory/" in n):
                    if z.read(n)[:40].split(b" ", 1)[-1].startswith(
                            b"serialization::archive"):
                        return True
    except Exception:
        pass
    return False


def _is_xpn(filepath):
    """A zip carrying an Expansion.xml manifest (Akai MPC expansion package)."""
    try:
        import zipfile
        with zipfile.ZipFile(filepath) as z:
            return "Expansion.xml" in z.namelist()
    except Exception:
        return False


def _is_xtd(filepath):
    """A gzip stream whose decompressed head is the ACVS magic (MPC3 .xtd)."""
    import gzip
    try:
        with gzip.open(filepath, "rb") as g:
            return g.read(4) == b"ACVS"
    except Exception:
        return False
