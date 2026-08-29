"""The anatomy pages' byte maps must be consistent with themselves.

Each map in `docs/formats/*-anatomy.html` declares a byte (or bit) array and a
set of fields with inclusive [lo,hi] ranges. Three things have to hold or the
picture is wrong regardless of what the format says:

  every field lies inside the array
  no two fields claim the same unit
  the ranges tile the span with no unexplained gap

An off-by-one in a hand-written range is invisible to a reader and fatal to
anyone using the page the way it asks to be used -- as a spec. These are the
only claims on the pages that can be checked without an external document, and
there are 128 maps across 33 pages, so checking them by hand is not a plan.

What this cannot check is whether a field's stated meaning is correct. That
needs a primary spec per format, and a cross-check against the kaitai_struct
specs covers 7 of the 33 (the rest have no .ksy at all).
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent
PAGES = ROOT / "docs" / "formats"

pytestmark = pytest.mark.skipif(not PAGES.is_dir(), reason="no docs/formats")


def _pages():
    return sorted(PAGES.glob("*-anatomy.html"))


def _maps(text):
    """[(mount, unit_count, [(label, lo, hi)])] for every byte map on a page.

    Two page shapes exist: a direct build("mount","byte",[bytes],[fields]) call
    and a lazy SPECS={"mount":["byte",[bytes],[fields]]} map that a tab builds
    on first show.
    """
    starts = [(m.group(1), m.group(2), m.end()) for m in re.finditer(
        r'build\(\s*["\']([\w-]+)["\']\s*,\s*["\'](byte|bit)["\']\s*,', text)]
    starts += [(m.group(1), m.group(2), m.end()) for m in re.finditer(
        r'["\']?([\w-]+)["\']?\s*:\s*\[\s*["\'](byte|bit)["\']\s*,', text)]

    out = []
    for mount, unit, pos in starts:
        rest = text[pos:]
        blocks, depth, start = [], 0, None
        for i, ch in enumerate(rest[:20000]):
            if ch == "[":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    blocks.append(rest[start:i + 1])
                    if len(blocks) == 2:
                        break
        if len(blocks) < 2:
            continue
        n = len(re.findall(r"0x[0-9A-Fa-f]{1,2}|\b\d+\b", blocks[0]))
        # a bit map addresses bits, so its span is eight times its byte count
        units = n * 8 if unit == "bit" else n
        fields = []
        for f in re.finditer(r'\{\s*label:\s*"([^"]*)"(.*?)\}(?=\s*,\s*\{|\s*\])',
                             blocks[1], re.S):
            r = re.search(r"r:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]", f.group(2))
            if r:
                fields.append((f.group(1), int(r.group(1)), int(r.group(2))))
        if fields:
            out.append((mount, units, fields))
    return out


def test_the_scan_finds_the_maps_it_claims_to_check():
    """Guards the guard: if the extractor stops matching, every test below
    passes by checking nothing."""
    pages = _pages()
    assert len(pages) >= 25, f"only {len(pages)} anatomy pages found"
    total = sum(len(_maps(p.read_text(encoding="utf-8"))) for p in pages)
    assert total >= 100, f"only {total} byte maps extracted; the parser is wrong"


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.stem)
def test_byte_map_ranges_are_consistent(page):
    text = page.read_text(encoding="utf-8")
    problems = []
    for mount, units, fields in _maps(text):
        owner = {}
        for label, lo, hi in fields:
            if hi < lo:
                problems.append(f"{mount}/{label}: reversed range [{lo},{hi}]")
                continue
            for b in range(lo, hi + 1):
                if b in owner:
                    problems.append(
                        f"{mount}: unit {b} claimed by both '{owner[b]}' "
                        f"and '{label}'")
                    break
                owner[b] = label
        if not owner:
            continue
        top = max(hi for _l, _lo, hi in fields)
        if units and top >= units:
            problems.append(
                f"{mount}: a field ends at {top} but the map declares "
                f"only {units} unit(s)")
        gaps = sorted(set(range(0, top + 1)) - set(owner))
        if gaps:
            problems.append(f"{mount}: unclaimed units {gaps[:12]}")
    assert not problems, f"{page.name}:\n  " + "\n  ".join(problems)


# Corpus counts are the one voice rule that can be matched reliably. These
# pages are finalized format specs, not audit reports: "accepting it gave 7 of
# 14 files a confidently wrong answer" is a statement about a test run and a
# tool, and it belongs in a changelog. Specimen filenames and the word acidcat
# are deliberately NOT matched here -- every page names acidcat a dozen times
# in its own chrome (title, footer, the theme-toggle component's CSS), and
# `._name.wav` is the AppleDouble convention rather than a specimen, so both
# produce more false positives than findings.
_CORPUS = re.compile(r"\b\d+\s+of\s+\d+\s+files\b|\b\d{2,}\s+specimens?\b", re.I)


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.stem)
def test_pages_do_not_quote_corpus_counts(page):
    raw = page.read_text(encoding="utf-8")
    raw = re.sub(r"<(script|style).*?</\1>", "", raw, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", raw)
    hits = _CORPUS.findall(text)
    assert not hits, (
        f"{page.name} quotes a corpus count ({hits}); these pages are format "
        f"specs, not audit reports")
