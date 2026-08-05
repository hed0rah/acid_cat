"""The lean-install invariant: the core stays usable with only mutagen.

acidcat's install tiers only mean something if the base install genuinely runs
without the optional stacks. These tests pin that boundary two ways: statically
(no core module imports a heavy dependency at module level) and behaviourally
(the core verbs still run, and a dep-gated verb prints an install hint instead
of a traceback).
"""

import ast
import builtins
import contextlib
import io
import os
import sys

import pytest

# the optional stacks, by extra. analysis/ is allowed to import its own stack
# lazily inside functions, but nothing may import it at module import time.
OPTIONAL = {"librosa", "numpy", "scipy", "soundfile", "textual", "mcp",
            "cryptography", "starlette", "uvicorn"}

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "acidcat")


def _module_level_imports(path):
    """Names imported at module level (not inside a function/class body)."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in tree.body:                     # top level only -- lazy imports are fine
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _core_modules():
    for root, _dirs, files in os.walk(os.path.join(SRC, "core")):
        if "__pycache__" in root:
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def test_core_has_no_module_level_heavy_imports():
    """No module under core/ may import an optional stack at module level.

    A lazy import inside the function that needs it is the contract -- that is
    what keeps `pip install acidcat` (mutagen only) a working install.
    """
    offenders = []
    for path in _core_modules():
        hits = _module_level_imports(path) & OPTIONAL
        if hits:
            rel = os.path.relpath(path, SRC)
            offenders.append(f"{rel}: {', '.join(sorted(hits))}")
    assert not offenders, (
        "optional dependencies imported at module level in core/:\n  "
        + "\n  ".join(offenders))


@contextlib.contextmanager
def _lean_install():
    """Make the optional stacks unimportable, as on a base install."""
    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name.split(".")[0] in OPTIONAL:
            raise ImportError(f"No module named {name!r} (lean install)")
        return real_import(name, *args, **kwargs)

    saved = {k: v for k, v in sys.modules.items()
             if k.split(".")[0] in OPTIONAL or k.startswith("acidcat")}
    for key in saved:
        del sys.modules[key]
    builtins.__import__ = guard
    try:
        yield
    finally:
        builtins.__import__ = real_import
        for key in [k for k in sys.modules if k.startswith("acidcat")]:
            del sys.modules[key]
        sys.modules.update(saved)


def _run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    from acidcat.cli import main
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()



@pytest.fixture
def lean_wav(tmp_path):
    """A valid WAV, generated here.

    These tests used to point at `data/test_formats/generated/src.wav`, which is
    gitignored, so on every CI runner seven of the nine skipped and the
    behavioural half of the lean-install invariant was never actually exercised
    -- only the static import scan ran. They do not need a particular specimen,
    just a well-formed file, so they make one.
    """
    import struct
    pcm = b"".join(struct.pack("<h", (i * 137) % 20000 - 10000) for i in range(512))
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    p = tmp_path / "src.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


@pytest.mark.parametrize("argv", [
    ["info", "{wav}"],
    ["inspect", "{wav}"],
    ["chunks", "{wav}"],
    ["probe", "{wav}", "strings"],
    ["validate", "{wav}"],
    ["formats"],
])
def test_core_verbs_run_without_optional_deps(argv, lean_wav):
    argv = [a.format(wav=lean_wav) for a in argv]
    with _lean_install():
        rc, _out, _err = _run_cli(argv)
    assert rc == 0, f"{argv[0]} failed on a lean install (rc={rc})"


def test_dep_gated_verb_hints_instead_of_crashing(lean_wav):
    """A verb whose stack is missing must say how to install it, not traceback."""
    with _lean_install():
        rc, out, err = _run_cli(["features", lean_wav])
    assert rc != 0
    assert "pip install acidcat[analysis]" in (err + out)


def test_filename_detection_survives_without_librosa(tmp_path):
    """BPM/key parsing from a filename is pure Python, so it must keep working
    when the analysis stack is absent (it is a capability, not a librosa
    feature)."""
    from acidcat.core.analysis.detect import (parse_bpm_from_filename,
                                              parse_key_from_path)
    p = tmp_path / "loop_128bpm_Am.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    assert parse_bpm_from_filename(str(p)) == 128
    assert parse_key_from_path(str(p)) == "Am"
