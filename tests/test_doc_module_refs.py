"""A doc that points at `core/foo.py` should point at a file that exists.

Modules moved during the 1.0 restructure -- sniff to core/infra/, indexing to
core/catalogue/, the DSP modules to core/analysis/, the codecs to core/codecs/ --
and 17 references across README and docs/ kept naming the old locations. Nothing
failed, because prose does not get imported. A reader following the doc just
found nothing there.

CHANGELOG.md is deliberately exempt. Its entries describe where a file was when
that version shipped, so "corrected" paths would make it a less accurate record,
not a more accurate one. A changelog is the one document that is supposed to go
stale.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent
SRC = ROOT / "src" / "acidcat"

# `core/x/y.py` or `src/acidcat/x/y.py` inside backticks
_REF = re.compile(r"`(core/[\w/]+\.py|src/acidcat/[\w/]+\.py)`")

_EXEMPT = {"CHANGELOG.md"}


def _live_docs():
    docs = [p for p in ROOT.glob("*.md") if p.name not in _EXEMPT]
    docs += sorted((ROOT / "docs").rglob("*.md"))
    return docs


def _resolve(ref):
    return (SRC / ref) if ref.startswith("core/") else (ROOT / ref)


def test_docs_were_actually_scanned():
    """Guards the guard.

    If the glob or the regex stops matching, every other test here passes by
    finding nothing -- a green suite that checked no documents. This asserts the
    scan has a corpus and that the corpus contains references to check.
    """
    docs = _live_docs()
    assert len(docs) >= 5, f"only {len(docs)} docs found; the glob is wrong"
    refs = sum(len(_REF.findall(p.read_text(encoding="utf-8", errors="replace")))
               for p in docs)
    assert refs >= 20, f"only {refs} module references found; the regex is wrong"


def test_every_referenced_module_exists():
    broken = []
    for p in _live_docs():
        for i, line in enumerate(
                p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in _REF.finditer(line):
                if not _resolve(m.group(1)).exists():
                    broken.append(f"{p.relative_to(ROOT)}:{i} -> {m.group(1)}")
    assert not broken, (
        "docs reference modules that do not exist:\n  " + "\n  ".join(broken))


def test_changelog_is_exempt_on_purpose():
    """Not a formality: this records WHY, so nobody 'fixes' the exemption.

    CHANGELOG.md contains stale paths by design. If someone removes the
    exemption the suite goes red on entries that are correct as history, and the
    tempting repair is to rewrite the log.
    """
    cl = ROOT / "CHANGELOG.md"
    if not cl.exists():
        pytest.skip("no CHANGELOG.md")
    assert "CHANGELOG.md" in _EXEMPT
    assert cl not in _live_docs()
