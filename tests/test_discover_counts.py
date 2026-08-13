"""A discovery count is a floor when the walk stopped above the files.

`discover_libraries` counts audio only within `max_depth` (default 3) and
reported that number as the pack's file count. A pack nesting one level deeper
came back as 520 against a true 657, and nothing in the answer suggested the
walk had stopped early -- so the number read as a total, an agent quoted it, and
the shortfall was invisible.

The rule this file pins: announce that a bound BIT, never that one exists. The
flag fires when audio actually sits below the cap, and stays quiet for an
ordinary deep-but-silent folder tree (artwork, documentation), or it becomes
noise on every pack and stops being read.
"""

import os
import struct

import pytest

from acidcat.core.catalogue.indexing import _count_audio_deep, _count_audio_in_subtree


def _wav(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", 8) + b"\x00" * 8)
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", len(body)) + body)


@pytest.fixture
def pack(tmp_path):
    """25 files one level down, 40 more four levels below that."""
    root = tmp_path / "pack"
    for i in range(25):
        _wav(str(root / "shallow" / f"s{i}.wav"))
    for i in range(40):
        _wav(str(root / "a" / "b" / "c" / "d" / f"deep{i}.wav"))
    return str(root)


class TestTheFlagIsRightAtEveryDepth:
    def test_a_cut_count_is_flagged(self, pack):
        count, truncated = _count_audio_deep(pack, max_depth=3)
        assert count == 25 and truncated is True

    def test_it_does_not_stop_at_the_first_pruned_directory(self, pack):
        """The bug in the first version of this flag.

        At max_depth=2 the directory pruned is `a/b/c`, which holds no files of
        its own -- the audio is one level below it. Checking only that
        directory reported "nothing below" while 40 files sat under it, which
        is the false all-clear the flag exists to prevent.
        """
        count, truncated = _count_audio_deep(pack, max_depth=2)
        assert count == 25
        assert truncated is True, "missed audio buried below the first prune"

    def test_a_complete_walk_is_not_flagged(self, pack):
        count, truncated = _count_audio_deep(pack, max_depth=9)
        assert count == 65 and truncated is False

    def test_the_boundary_is_where_the_files_are(self, pack):
        """Exactly at the depth that first reaches them, it must go quiet."""
        assert _count_audio_deep(pack, max_depth=3)[1] is True
        assert _count_audio_deep(pack, max_depth=4) == (65, False)

    def test_a_deep_tree_with_no_audio_does_not_cry_wolf(self, tmp_path):
        """Artwork and docs nest deeply in every commercial pack. A flag that
        fires on those fires on everything."""
        root = tmp_path / "quiet"
        _wav(str(root / "one.wav"))
        deep = root / "art" / "x" / "y" / "z"
        deep.mkdir(parents=True)
        (deep / "cover.txt").write_text("not audio")
        assert _count_audio_deep(str(root), max_depth=1) == (1, False)

    def test_junk_files_below_the_cap_are_not_audio(self, tmp_path):
        """macOS ._ resource forks carry the same extension as the file they
        shadow, and counting them would flag a pack that holds nothing extra."""
        root = tmp_path / "junky"
        _wav(str(root / "one.wav"))
        deep = root / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "._ghost.wav").write_bytes(b"\x00" * 16)
        assert _count_audio_deep(str(root), max_depth=1) == (1, False)


def test_the_plain_counter_still_answers_the_old_question(tmp_path):
    """_count_audio_in_subtree is what the qualification test calls, and its
    contract -- one integer, counted within the cap -- has not changed."""
    root = tmp_path / "p"
    for i in range(5):
        _wav(str(root / f"s{i}.wav"))
    _wav(str(root / "a" / "b" / "c" / "d" / "buried.wav"))
    assert _count_audio_in_subtree(str(root), max_depth=2) == 5
    assert _count_audio_in_subtree(str(root), max_depth=9) == 6


class TestTheMcpAnswerCarriesIt:
    def test_a_cut_candidate_is_marked_and_the_result_says_why(self, tmp_path,
                                                               monkeypatch):
        pytest.importorskip("mcp")
        from acidcat.mcp_server import handlers

        root = tmp_path / "libs"
        for i in range(25):
            _wav(str(root / "packA" / "shallow" / f"s{i}.wav"))
        for i in range(40):
            _wav(str(root / "packA" / "a" / "b" / "c" / "d" / f"d{i}.wav"))
        monkeypatch.setattr(handlers, "_REGISTRY_PATH",
                            str(tmp_path / "reg.db"), raising=False)

        out = handlers.discover_libraries({
            "root": str(root), "dry_run": True,
            "min_samples": 20, "max_depth": 3,
        })
        cands = out["candidates"]
        assert cands, out
        assert any(c.get("audio_count_is_a_floor") for c in cands), cands
        assert "note" in out and "floor" in out["note"]
        assert out["max_depth"] == 3

    def test_a_complete_scan_carries_no_note(self, tmp_path, monkeypatch):
        """The other half: silence is the claim that the count is a total."""
        pytest.importorskip("mcp")
        from acidcat.mcp_server import handlers

        root = tmp_path / "libs"
        for i in range(25):
            _wav(str(root / "packB" / f"s{i}.wav"))
        monkeypatch.setattr(handlers, "_REGISTRY_PATH",
                            str(tmp_path / "reg.db"), raising=False)

        out = handlers.discover_libraries({
            "root": str(root), "dry_run": True,
            "min_samples": 20, "max_depth": 3,
        })
        assert "note" not in out, out
        assert not any(c.get("audio_count_is_a_floor")
                       for c in out["candidates"])
