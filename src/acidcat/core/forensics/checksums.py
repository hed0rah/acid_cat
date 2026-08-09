"""Verify the integrity data a format carries about itself.

`integrity.py` compares what a container *declares* against what the audio *is*.
This module is narrower and stronger: some formats ship a checksum over their own
bytes, so damage is **provable** rather than inferred -- no statistics, no
confidence level, no room for argument.

Two are checkable without decoding anything, which matters because acidcat
bundles no audio decoders:

  FLAC   every frame carries a CRC-8 over its header and a CRC-16 over the whole
         frame. Both are computed on raw bytes. (STREAMINFO's MD5 is over the
         *decoded* stream and stays out of reach -- see flacrepair.)
  MP3    frames are not checksummed by default, but they are heavily
         self-describing: a sync word, a length derivable from the header, and
         side-info fields with spec-defined bounds. Damage shows up as a frame
         that cannot be where the previous frame's length says it is.

The distinction is worth keeping sharp in the output. A failed CRC is proof. A
failed structural check is very strong evidence but not proof, because a file
can be unusual without being damaged.
"""

_READ_CAP = 64 * 1024 * 1024      # bytes scanned; past this the count is partial
_MAX_FINDINGS = 50                # reported individually; the rest are counted
_LOOKAHEAD_CANDS = 8              # spurious CRC-8 hits to step over
_MAX_FRAME = 1 << 20              # no real FLAC frame approaches this


def _crc8_table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        t.append(c)
    return t


def _crc16_table():
    t = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x8005) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
        t.append(c)
    return t


_CRC8 = _crc8_table()
_CRC16 = _crc16_table()


def crc8(data):
    """FLAC frame-header CRC: x^8 + x^2 + x + 1, init 0."""
    c = 0
    for b in data:
        c = _CRC8[c ^ b]
    return c


def crc16(data):
    """FLAC frame CRC: x^16 + x^15 + x^2 + 1, init 0."""
    c = 0
    for b in data:
        c = _CRC16[((c >> 8) ^ b) & 0xFF] ^ ((c << 8) & 0xFFFF)
    return c


# ------------------------------------------------------------------ FLAC

def _flac_header_len(data, pos):
    """Length of the frame header at `pos` including its CRC-8 byte, or None.

    The header is variable-length: a UTF-8-style coded number, then optional
    block-size and sample-rate bytes selected by nibbles in byte 2.
    """
    if pos + 5 > len(data):
        return None
    if data[pos] != 0xFF or (data[pos + 1] & 0xFC) != 0xF8:
        return None                       # 14-bit sync, then a zero reserved bit
    bs = (data[pos + 2] >> 4) & 0x0F
    sr = data[pos + 2] & 0x0F
    if bs == 0 or sr == 0x0F:
        return None                       # reserved / invalid
    if (data[pos + 3] & 0x01) != 0:
        return None                       # reserved bit must be zero
    n = pos + 4
    first = data[n] if n < len(data) else None
    if first is None:
        return None
    if first < 0x80:
        n += 1
    else:                                 # count the leading ones
        extra = 0
        for bit in range(6, 0, -1):
            if first & (1 << bit):
                extra += 1
            else:
                break
        if extra < 1 or extra > 6:
            return None
        n += 1 + extra
    if bs == 0x06:
        n += 1
    elif bs == 0x07:
        n += 2
    if sr == 0x0C:
        n += 1
    elif sr in (0x0D, 0x0E):
        n += 2
    return (n + 1) - pos                  # +1 for the CRC-8 byte itself


def _flac_frame_starts(data, start, end):
    """Offsets whose frame header parses AND whose CRC-8 checks out.

    The CRC-8 is what makes this reliable: 0xFF 0xF8 occurs by chance in audio
    payload, but a chance hit that also satisfies its own header checksum is
    rare enough to ignore.
    """
    out = []
    pos = start
    # A frame sync always begins 0xFF, so let bytes.find do the skipping in C.
    # Stepping one byte at a time called the header parser once per byte --
    # 2.3 million times on a 2.3 MB file, which was 80% of the runtime. 0xFF is
    # roughly 1 byte in 256 of compressed audio, so this is ~250x fewer calls
    # and finds exactly the same candidates.
    view = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    while pos < end - 5:
        pos = view.find(b"\xFF", pos, end - 4)
        if pos < 0:
            break
        hlen = _flac_header_len(data, pos)
        if hlen and pos + hlen <= end:
            if crc8(data[pos:pos + hlen - 1]) == data[pos + hlen - 1]:
                out.append(pos)
                pos += hlen
                continue
        pos += 1
    return out


def flac_frames(data, audio_start, file_size=None):
    """Verify every FLAC frame's CRC-16.

    A failed CRC-16 is proof of damage: the encoder wrote a checksum over these
    exact bytes and they no longer match it.

    The subtlety is finding where each frame *ends*, since FLAC frames are
    variable length and carry no length field. A valid-looking sync plus a
    passing CRC-8 is not sufficient -- 0xFF 0xF8 occurs inside audio payload,
    and such a hit can satisfy an 8-bit checksum by chance often enough to
    matter. Taking every CRC-8 hit as a boundary split real frames in two and
    produced two false failures on an undamaged file.

    So the CRC-16 arbitrates the boundary, which is exactly what a checksum
    over the whole frame is for: from a known-good start, the true end is the
    first candidate whose CRC-16 validates. Only when no candidate validates is
    the frame reported as damaged.
    """
    end = min(len(data), audio_start + _READ_CAP)
    partial = end < len(data)
    cands = _flac_frame_starts(data, audio_start, end)
    res = {"checked": 0, "failed": 0, "offsets": [], "truncated": False,
           "partial": partial, "frames_found": 0}
    if not cands:
        return res

    idx = 0
    while idx < len(cands):
        pos = cands[idx]
        hit = None
        # Bounded lookahead keeps this linear. A spurious CRC-8 hit inside
        # payload is usually one or two candidates away, and a real frame is
        # far smaller than the byte cap, so a match beyond either bound means
        # this frame does not verify rather than that the search was too short.
        j = idx + 1
        while j < len(cands) and j - idx <= _LOOKAHEAD_CANDS:
            b = cands[j]
            if b - pos > _MAX_FRAME:
                break
            if b - pos >= 4:
                stored = (data[b - 2] << 8) | data[b - 1]
                if crc16(data[pos:b - 2]) == stored:
                    hit = j
                    break
            j += 1
        if hit is not None:
            res["checked"] += 1
            res["frames_found"] += 1
            idx = hit
            continue
        # no successor verified -- try EOF, since the last frame has none
        if end - pos >= 4:
            stored = (data[end - 2] << 8) | data[end - 1]
            if crc16(data[pos:end - 2]) == stored:
                res["checked"] += 1
                res["frames_found"] += 1
                break
        res["checked"] += 1
        res["frames_found"] += 1
        res["failed"] += 1
        if len(res["offsets"]) < _MAX_FINDINGS:
            res["offsets"].append(pos)
        idx += 1                              # resync and keep going
    if res["failed"] and file_size is not None and end >= file_size:
        # a final frame cut mid-write has no CRC-16 to fail against; that is
        # truncation, which is a different claim from corruption
        res["truncated"] = res["offsets"][-1] == cands[-1] if res["offsets"] else False
    return res


# ------------------------------------------------------------------- MP3

_MP3_BITRATE_V1L3 = (None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224,
                     256, 320, None)
_MP3_RATE_V1 = (44100, 48000, 32000, None)


def _mp3_header(data, pos):
    """(frame_len, channel_mode) for an MPEG-1 Layer III frame, or None."""
    if pos + 4 > len(data) or data[pos] != 0xFF or (data[pos + 1] & 0xE0) != 0xE0:
        return None
    if ((data[pos + 1] >> 3) & 0x03) != 3:        # MPEG-1 only
        return None
    if ((data[pos + 1] >> 1) & 0x03) != 1:        # Layer III only
        return None
    br = _MP3_BITRATE_V1L3[(data[pos + 2] >> 4) & 0x0F]
    sr = _MP3_RATE_V1[(data[pos + 2] >> 2) & 0x03]
    if not br or not sr:
        return None
    pad = (data[pos + 2] >> 1) & 0x01
    return (144 * br * 1000 // sr) + pad, (data[pos + 3] >> 6) & 0x03


def _mp3_side_info(data, pos, mode):
    """(main_data_begin, worst big_values) or None.

    Reads only as far as the fields with spec-defined bounds, then asserts the
    side info was consumed exactly -- 256 bits stereo, 136 mono. That assertion
    is not decoration: an off-by-N in a bit reader produces confident garbage
    that is indistinguishable from real corruption.
    """
    off = pos + 4
    if not (data[pos + 1] & 0x01):                # protection bit clear = CRC
        off += 2
    mono = mode == 3
    nch = 1 if mono else 2
    need = off + (17 if mono else 32)
    if need > len(data):
        return None
    bit = off * 8

    def read(n):
        nonlocal bit
        v = 0
        for _ in range(n):
            v = (v << 1) | ((data[bit >> 3] >> (7 - (bit & 7))) & 1)
            bit += 1
        return v

    mdb = read(9)
    read(5 if mono else 3)                        # private_bits
    read(4 * nch)                                 # scfsi
    worst = 0
    for _gr in range(2):
        for _ch in range(nch):
            read(12)                              # part2_3_length
            worst = max(worst, read(9))           # big_values
            read(8)                               # global_gain
            read(4)                               # scalefac_compress
            read(1)                               # window_switching_flag
            read(22)                              # both branches are 22 bits
            read(3)                               # preflag/scale/count1
    if bit - off * 8 != (136 if mono else 256):
        return None
    return mdb, worst


def mp3_frames(data, audio_start):
    """Walk MP3 frames by validating each one, not by striding the bitrate.

    Counting frames as `size / frame_len` reports a confident, specific, wrong
    answer for a damaged file -- the count comes out identical to an undamaged
    one. Three checks, all cheap:

      resync         a frame does not begin where the previous one's length says
      bad_backref    `main_data_begin` points before any audio exists. MP3 frames
                     are not self-contained -- a frame's Huffman data can live in
                     previous frames' bytes, up to 511 back -- so a dangling
                     back-reference is evidence of a cut or splice.
      bad_bigvalues  `big_values` counts spectral PAIRS, so 2*big_values must fit
                     a granule's 576 lines. Over 288 is impossible, not unusual.
    """
    end = min(len(data), audio_start + _READ_CAP)
    res = {"frames": 0, "resyncs": 0, "bad_backref": 0, "bad_bigvalues": 0,
           "offsets": [], "partial": end < len(data), "preamble": 0}
    pos = audio_start
    while pos + 4 <= end:
        h = _mp3_header(data, pos)
        if h is None:
            nxt = pos + 1
            while nxt + 4 <= end and _mp3_header(data, nxt) is None:
                nxt += 1
            if nxt + 4 > end:
                break
            if res["frames"] == 0:
                # Bytes before the FIRST frame are a preamble, not a break in
                # the stream. Leading nulls or an unrecognised tag are ordinary:
                # four real MP3s out of 600 open with a run of zeros, and
                # counting that as damage made every one of them a false
                # positive. Damage means the stream broke after it started.
                res["preamble"] = nxt - pos
            else:
                res["resyncs"] += 1
                if len(res["offsets"]) < _MAX_FINDINGS:
                    res["offsets"].append(pos)
            pos = nxt
            continue
        flen, mode = h
        si = _mp3_side_info(data, pos, mode)
        if si is not None:
            mdb, worst = si
            if pos - mdb < audio_start:
                res["bad_backref"] += 1
                if len(res["offsets"]) < _MAX_FINDINGS:
                    res["offsets"].append(pos)
            if worst > 288:
                res["bad_bigvalues"] += 1
                if len(res["offsets"]) < _MAX_FINDINGS:
                    res["offsets"].append(pos)
        res["frames"] += 1
        pos += flen
    return res
