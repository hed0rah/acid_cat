"""The suite must not write into the user's real home.

Deleting ACIDCAT_REGISTRY/ACIDCAT_DB was the isolation, and it was the bug:
with them unset, `paths.acidcat_home()` falls back to
`os.path.expanduser("~")`, so every test that indexed anything wrote a database
into the user's real `~/.acidcat/libraries/`. Two audit runs plus the suite
left 1,786 orphaned .db files there -- 126 MB, against 32 genuinely registered
libraries.

conftest now sets a throwaway HOME/USERPROFILE instead. These tests pin that,
because the failure is silent: nothing breaks, the mess just accumulates in
somebody else's home directory.
"""

import os

from acidcat.core.catalogue import paths


def test_home_is_redirected_for_tests(tmp_path):
    """The fixture is autouse, so this holds in every test in the suite."""
    home = os.path.expanduser("~")
    assert "acidcat_home" in home.replace("\\", "/"), (
        f"expanduser('~') is {home} -- tests are pointed at a real home")


def test_the_library_directory_lands_under_the_fake_home():
    """The specific path that leaked: per-library DBs resolve through
    acidcat_home(), not through ACIDCAT_REGISTRY, so redirecting the registry
    alone never contained them."""
    lib = paths.central_db_path_for("probe_label", "/some/root")
    resolved = os.path.abspath(lib).replace("\\", "/")
    assert "acidcat_home" in resolved, (
        f"a library DB would be written to {resolved}")


def test_the_registry_lands_under_the_fake_home():
    reg = os.path.abspath(paths.resolve_registry_path()).replace("\\", "/")
    assert "acidcat_home" in reg, f"the registry resolves to {reg}"


def test_indexing_writes_nothing_outside_the_fake_home(tmp_path):
    """End to end: index a real directory and confirm every byte written stays
    inside the throwaway home."""
    import struct
    from acidcat.cli import main

    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", 200) + bytes(200))
    src = tmp_path / "lib"
    src.mkdir()
    (src / "a.wav").write_bytes(b"RIFF" + struct.pack("<I", len(body) + 4)
                                + b"WAVE" + body)

    home = os.path.expanduser("~")
    before = set()
    libdir = os.path.join(home, ".acidcat", "libraries")
    if os.path.isdir(libdir):
        before = set(os.listdir(libdir))

    assert main(["index", str(src), "--label", "isolation_probe"]) in (0, 1)

    assert os.path.isdir(libdir), "indexing wrote no library at all"
    created = set(os.listdir(libdir)) - before
    assert created, "indexing created no database -- is the test still valid?"
    for name in created:
        full = os.path.join(libdir, name).replace("\\", "/")
        assert "acidcat_home" in full, f"{full} is outside the fake home"
