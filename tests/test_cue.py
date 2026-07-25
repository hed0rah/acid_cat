"""Tests for CUE parsing and CD-DA extraction -- both the single-.bin layout
(tracks at cumulative MSF offsets) and the split layout (one .bin per track)."""
import io
import wave

from acidcat.core import cue


def test_cue_single_bin(tmp_path):
    (tmp_path / "g.bin").write_bytes(bytes(2352 * 450))
    (tmp_path / "g.cue").write_text(
        'FILE "g.bin" BINARY\n'
        "  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n"
        "  TRACK 02 AUDIO\n    INDEX 01 00:02:00\n"     # lba 150
        "  TRACK 03 AUDIO\n    INDEX 01 00:04:00\n")    # lba 300
    tracks = cue.parse(str(tmp_path / "g.cue"))
    assert [t["num"] for t in tracks] == [1, 2, 3]
    assert tracks[1]["start_lba"] == 150

    at = list(cue.audio_tracks(str(tmp_path / "g.cue")))
    assert [t["num"] for t in at] == [2, 3]
    assert at[0]["start"] == 150 * 2352 and at[0]["size"] == 150 * 2352   # 150..300
    assert at[1]["start"] == 300 * 2352 and at[1]["size"] == 150 * 2352   # 300..EOF(450)


def test_cue_split_bins_skips_pregap(tmp_path):
    (tmp_path / "t1.bin").write_bytes(bytes(2352 * 100))
    (tmp_path / "t2.bin").write_bytes(bytes(2352 * 200))
    (tmp_path / "g.cue").write_text(
        'FILE "t1.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
        'FILE "t2.bin" BINARY\n  TRACK 02 AUDIO\n'
        "    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n")
    at = list(cue.audio_tracks(str(tmp_path / "g.cue")))
    assert len(at) == 1
    assert at[0]["num"] == 2
    assert at[0]["start"] == 150 * 2352                # INDEX 01 skips the 2s pregap
    assert at[0]["size"] == (200 - 150) * 2352         # to end of the track file


def test_cue_extract_wires(tmp_path):
    from acidcat.core import sniff as sniffmod
    from acidcat.core import samples as smod

    (tmp_path / "t1.bin").write_bytes(bytes(2352 * 10))          # data track
    (tmp_path / "t2.bin").write_bytes(b"\x11\x22" * (2352 * 20 // 2))   # audio
    (tmp_path / "g.cue").write_text(
        'FILE "t1.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
        'FILE "t2.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n')
    assert sniffmod.sniff(str(tmp_path / "g.cue")) == "cue"
    recs = list(smod.iter_samples(str(tmp_path / "g.cue")))
    assert len(recs) == 1 and recs[0]["name"] == "track_02"
    w = wave.open(io.BytesIO(recs[0]["wav"]))
    assert w.getnchannels() == 2 and w.getframerate() == 44100
