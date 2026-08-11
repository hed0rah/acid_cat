"""A break in acidcat must never look like a property of the file.

`extract_audio_features` wrapped its whole body in one `except Exception as e:
return None` and discarded `e`. That is the sole feature path for the entire
index and similarity layer, so any programming bug -- or any librosa rename --
made a whole reindex store nothing, report success, and leave `similar` and MCP
`find_similar` returning empty results indistinguishable from a corpus with no
analyzable audio.

The precedent is in this repo. `detect.py` documents that `librosa.beat.tempo`
became `librosa.feature.tempo`, that the call sat inside a catch-all, and that
BPM therefore "went silently filename-only while still reporting bpm_source
detected". `features.py` calls that exact symbol, and pyproject pins only
`librosa>=0.10.1` with no upper bound.

The split: a file that cannot be DECODED is a real per-file None and stays
quiet. Anything else is announced.
"""

import struct

import pytest

pytest.importorskip("librosa")
pytest.importorskip("numpy")


def _wav(path, secs=0.4, rate=22050):
    import math
    n = int(rate * secs)
    pcm = b"".join(struct.pack("<h", int(9000 * math.sin(i / 20.0)))
                   for i in range(n))
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate,
                                            rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


@pytest.fixture
def good(tmp_path):
    return _wav(tmp_path / "a.wav")


def test_a_healthy_file_still_extracts(good):
    from acidcat.core.analysis.features import extract_audio_features
    feats = extract_audio_features(good)
    assert feats and len(feats) > 40


def test_a_librosa_break_is_announced(good, capsys, monkeypatch):
    """The regression this exists for: simulate the rename that already bit
    detect.py, on the symbol features.py actually calls."""
    import librosa
    from acidcat.core.analysis import features as F

    def gone(*a, **k):
        raise AttributeError("module 'librosa.feature' has no attribute 'tempo'")
    monkeypatch.setattr(librosa.feature, "tempo", gone)

    before = len(F.EXTRACTION_FAILURES)
    assert F.extract_audio_features(good) is None      # still degrades
    err = capsys.readouterr().err
    assert "feature extraction failed" in err          # but says so
    assert "AttributeError" in err                     # naming the cause
    assert len(F.EXTRACTION_FAILURES) == before + 1
    assert F.EXTRACTION_FAILURES[-1]["fatal"] is True


def test_a_programming_bug_is_announced_too(good, capsys, monkeypatch):
    """Not just import-time breaks: any exception that is not a decode failure
    is a bug we want to hear about."""
    import librosa
    from acidcat.core.analysis import features as F
    monkeypatch.setattr(librosa.feature, "tonnetz",
                        lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")))
    assert F.extract_audio_features(good) is None
    assert "TypeError" in capsys.readouterr().err


def test_an_undecodable_file_stays_quiet(tmp_path, capsys):
    """The other half. A file that genuinely is not audio is a per-file answer,
    not a systemic break, and must not spam stderr during a library sweep."""
    from acidcat.core.analysis.features import extract_audio_features
    p = tmp_path / "notaudio.bin"
    p.write_bytes(bytes(range(256)) * 8)
    assert extract_audio_features(str(p)) is None
    assert "feature extraction failed" not in capsys.readouterr().err


def test_too_short_stays_quiet(tmp_path, capsys):
    """The documented short-file None, which was correct all along."""
    from acidcat.core.analysis.features import extract_audio_features
    assert extract_audio_features(_wav(tmp_path / "t.wav", secs=0.005)) is None
    assert capsys.readouterr().err == ""


def test_classify_closes_its_handle_when_the_read_fails(tmp_path, monkeypatch):
    """_Bytes opened the file then read it unguarded, so an I/O error in
    __init__ escaped before the object reached its `with` -- leaking the
    descriptor, which on Windows keeps the file locked."""
    from acidcat.core.forensics import classify as C
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00" * 64)

    real_open = open
    holder = {}

    def spy(*a, **k):
        f = real_open(*a, **k)
        holder["f"] = f
        if a and str(a[0]).endswith("x.bin") and "b" in (a[1] if len(a) > 1 else ""):
            f.read = lambda *_: (_ for _ in ()).throw(OSError("device error"))
        return f

    monkeypatch.setattr("builtins.open", spy)
    with pytest.raises(OSError):
        C._Bytes(str(p))
    monkeypatch.undo()
    assert holder["f"].closed, "handle leaked out of a failing __init__"
