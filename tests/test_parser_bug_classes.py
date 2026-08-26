"""Historical audio-parser bug classes, as benign fixtures.

acidcat is pure Python, so the memory-corruption CVEs behind these cannot become
code execution here. What survives the translation is the INPUT PATTERN -- a
forged count, a zero-size element, a channel count of nought, an odd UTF-16
body, a duration inflated past the file -- and in Python those become a hang, a
huge allocation, or the worst of the three: a confidently wrong answer.

That last one is the reason this file exists rather than a fuzzer. Fuzzing finds
crashes. Nothing here crashes. Every one of these parses cleanly and the
question is whether what comes back is TRUE.

Ported from the playground's cve_immunity.py, which ran these through the CLI in
a subprocess to survive hangs. In-process is better here: pytest already bounds
the run, and calling the walker directly means a fixture can assert what the
parser SAID, not just that it exited zero.

Measured against 1.2.2 when this landed: all six survived in about 10 ms each,
three already carried the right warning, two were correctly silent -- and one
reported an impossibility as a plain fact. That one is fixed in ogg.py; the
fixture stays so it cannot come back.
"""

import struct

import pytest

from acidcat.core.walk import walk_file


def _chunk(cid, payload):
    return (cid + struct.pack("<I", len(payload)) + payload
            + (b"\x00" if len(payload) % 2 else b""))


def _wav(*chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


_FMT = _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16))
_DATA = _chunk(b"data", b"\x00" * 16)


def _write(tmp_path, name, blob):
    p = tmp_path / name
    p.write_bytes(blob)
    return str(p)


def _fields(chunks):
    out = {}
    for c in chunks:
        for f in c.get("fields", []):
            out.setdefault(f.get("name"), f)
    return out


def _all_warnings(chunks, warns):
    return list(warns) + [w for c in chunks for w in c.get("warnings", [])]


# ── forged counts: the allocation never happens ─────────────────────

class TestForgedCounts:
    """CVE-2018-10536 and the Stagefright family: a count field multiplied into
    an allocation before anyone checks the payload can hold that many."""

    def test_a_forged_loop_count_is_bounded_by_the_payload(self, tmp_path):
        smpl = struct.pack("<9I", 0, 0, 0, 60, 0, 0, 0, 0xFFFFFFFF, 0)
        p = _write(tmp_path, "smpl.wav", _wav(_FMT, _DATA, _chunk(b"smpl", smpl)))
        _label, chunks, warns = walk_file(p)
        text = " ".join(str(w) for w in _all_warnings(chunks, warns))
        assert "4294967295" in text and "0" in text, (
            "a chunk claiming 4.3 billion loops in nine bytes was accepted "
            "without comment: %s" % text)

    def test_a_forged_cue_count_does_not_drive_a_loop(self, tmp_path):
        p = _write(tmp_path, "cue.wav",
                   _wav(_FMT, _DATA, _chunk(b"cue ", struct.pack("<I", 0xFFFFFFFF))))
        _label, chunks, warns = walk_file(p)
        text = " ".join(str(w) for w in _all_warnings(chunks, warns))
        assert "4294967295" in text, (
            "4.3 billion cue points were claimed and nothing said so")

    def test_a_duration_inflated_past_the_file_is_flagged(self, tmp_path):
        body = (b"WAVE" + _FMT + b"data" + struct.pack("<I", 0x7FFFFFF0)
                + b"\x00" * 16)
        blob = b"RIFF" + struct.pack("<I", len(body)) + body
        _label, chunks, warns = walk_file(_write(tmp_path, "inf.wav", blob))
        text = " ".join(str(w) for w in _all_warnings(chunks, warns))
        assert "2,147,483,632" in text or "2147483632" in text, (
            "a data chunk claiming 2 GB inside a 60-byte file went unremarked")


# ── an impossibility is not a fact ──────────────────────────────────

class TestImpossibleValues:
    """The class fuzzing cannot see. Nothing crashes, nothing hangs, and the
    answer is wrong -- which is worse, because it looks like an answer."""

    def test_zero_channels_is_reported_as_impossible(self, tmp_path):
        """libvorbis CVE-2017-14632/14633 shape: an identification header
        declaring no channels.

        Before this landed acidcat printed `channels: 0` with no comment.
        Nothing downstream can divide by that, and a reader seeing it bare has
        no way to tell a parse failure from a file that really says nought.
        """
        ident = (b"\x01vorbis" + struct.pack("<I", 0) + bytes([0])
                 + struct.pack("<I", 0))
        hdr = (b"OggS\x00\x02" + b"\x00" * 8 + struct.pack("<I", 1)
               + b"\x00" * 4 + b"\x00" * 4 + bytes([1]) + bytes([len(ident)]))
        p = _write(tmp_path, "zero.ogg", hdr + ident)
        _label, chunks, warns = walk_file(p)
        field = _fields(chunks).get("channels")
        assert field is not None, "the channel count was dropped, hiding the evidence"
        assert field.get("value") == 0, "the file says 0; report what it says"
        note = str(field.get("note") or field.get("desc") or "")
        assert "impossible" in note.lower(), (
            "the field itself reads as a plain fact; anyone looking at the "
            "value alone has nothing telling them it cannot be true: %r" % note)
        text = " ".join(str(w) for w in _all_warnings(chunks, warns))
        assert "0 channels" in text.lower(), (
            "no file-level warning: the note is only seen by someone already "
            "reading that field, and a caller summarising the walk sees "
            "nothing at all: %r" % text)


# ── survival, where survival is the whole assertion ─────────────────

class TestSurvival:
    """These have no right answer to check -- the C bugs behind them were
    infinite loops, so the assertion is that the walker returns at all and does
    not invent anything on the way."""

    def test_an_odd_utf16_frame_body_terminates(self, tmp_path):
        """libid3tag CVE-2017-11551: a UTF-16 body of odd length. The C decoder
        read past the end looking for a pair that was never there."""
        fbody = b"\x01\xff\xfeA"
        frame = b"TXXX" + struct.pack(">I", len(fbody)) + b"\x00\x00" + fbody
        n = len(frame)
        synch = bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F,
                       (n >> 7) & 0x7F, n & 0x7F])
        blob = (b"ID3\x04\x00\x00" + synch + frame
                + b"\xff\xfb\x90\x00" + b"\x00" * 417)
        _label, chunks, _warns = walk_file(_write(tmp_path, "odd.mp3", blob))
        rate = _fields(chunks).get("sample_rate")
        assert rate and rate.get("value") == 44100, (
            "the frame header after an odd UTF-16 tag was misread")

    def test_a_zero_size_subchunk_does_not_spin(self, tmp_path):
        """The FFmpeg AVI shape: a zero-size element inside a list. A reader
        that advances by the size field alone never advances."""
        lst = (b"INFO" + b"IART" + struct.pack("<I", 0)
               + b"INAM" + struct.pack("<I", 2) + b"hi")
        p = _write(tmp_path, "list.wav", _wav(_FMT, _DATA, _chunk(b"LIST", lst)))
        _label, chunks, _warns = walk_file(p)
        got = _fields(chunks)
        assert got.get("channels", {}).get("value") == 2
        assert got.get("sample_rate", {}).get("value") == 44100, (
            "a zero-size sub-chunk disturbed the fields around it")
