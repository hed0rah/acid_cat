"""ACIDCAT_HOME relocates ALL catalogue state, not just the registry.

ACIDCAT_REGISTRY moves `registry.db` and nothing else. The per-library index
DBs live in `<home>/libraries/` and kept landing in the real home regardless, so
setting ACIDCAT_REGISTRY to a temp path -- the obvious way to try a scratch
catalogue, and the recipe this project handed its own auditors -- still wrote
into `~/.acidcat/libraries/`. That is how roughly 1,800 stray DBs accumulated
there before anyone noticed.

Isolation should be one variable, not a list of them.
"""

import os
import struct
import subprocess
import sys

import pytest


def _wav(path):
    pcm = b"\x11\x22" * 128
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


@pytest.fixture
def lib(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    _wav(d / "a.wav")
    return d


def test_acidcat_home_moves_everything(tmp_path, lib, monkeypatch):
    home = tmp_path / "home"
    env = dict(os.environ, ACIDCAT_HOME=str(home))
    # conftest sets ACIDCAT_REGISTRY for the whole suite and it is the more
    # specific variable, so it would legitimately win here and this test would
    # be asserting the wrong thing
    env.pop("ACIDCAT_REGISTRY", None)
    r = subprocess.run([sys.executable, "-m", "acidcat", "index", str(lib),
                        "--label", "iso"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr

    assert (home / "registry.db").exists()
    dbs = list((home / "libraries").glob("*.db"))
    assert len(dbs) == 1, "the per-library DB did not follow ACIDCAT_HOME"


def test_paths_all_derive_from_the_same_root(tmp_path, monkeypatch):
    """Every path helper must agree, or isolation is partial in a way that is
    invisible until something writes."""
    from acidcat.core.catalogue import paths
    # conftest sets ACIDCAT_REGISTRY to isolate the suite, and that is MORE
    # specific than ACIDCAT_HOME, so it legitimately wins for that one file.
    # Clear it to test the HOME-only case.
    monkeypatch.delenv("ACIDCAT_REGISTRY", raising=False)
    monkeypatch.setenv("ACIDCAT_HOME", str(tmp_path / "h"))
    root = paths.acidcat_home()
    assert paths.central_libraries_dir().startswith(root)
    assert paths.registry_db_path().startswith(root)
    assert paths.resolve_registry_path().startswith(root)


def test_unset_falls_back_to_the_home_directory(monkeypatch):
    """With nothing set, the path is <expanduser("~")>/.acidcat -- whatever HOME
    happens to be. conftest points HOME at a temp dir for the whole suite, so
    assert the SHAPE rather than a literal path."""
    from acidcat.core.catalogue import paths
    monkeypatch.delenv("ACIDCAT_HOME", raising=False)
    got = paths.acidcat_home()
    assert got.endswith("/.acidcat")
    assert got.startswith(os.path.expanduser("~").replace("\\", "/"))


def test_registry_env_still_wins_for_the_registry(tmp_path, monkeypatch):
    """ACIDCAT_REGISTRY keeps its meaning -- it is more specific, so it beats
    ACIDCAT_HOME for the one file it names. Anyone relying on it is unaffected."""
    from acidcat.core.catalogue import paths
    monkeypatch.setenv("ACIDCAT_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("ACIDCAT_REGISTRY", str(tmp_path / "elsewhere.db"))
    assert paths.resolve_registry_path().endswith("elsewhere.db")
    # ...but the libraries still follow HOME
    assert paths.central_libraries_dir().startswith(str(tmp_path / "h").replace("\\", "/"))
