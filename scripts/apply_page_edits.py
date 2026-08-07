"""Apply reviewed find/replace edits to the anatomy pages, safely.

The edits are prose: an anatomy page states what the format IS, so provenance
counts, tool behaviour and research narrative come out. Nothing factual should
move, and the one thing that must NEVER move is a byte value in a byte map.

So this checks, per file:
  * every `old` occurs exactly once before it is replaced
  * every numeric byte array in every build()/SPECS map is byte-identical after
  * the set of map ids is unchanged
and refuses to write the file if any of that fails.

    python scripts/apply_page_edits.py edits_batch1.json [more.json ...]
    python scripts/apply_page_edits.py --dry-run edits.json
"""
import json
import pathlib
import re
import sys

DOCS = pathlib.Path("docs/formats")


def byte_arrays(text):
    """Every numeric array in the page, as a comparable structure."""
    out = []
    for m in re.finditer(r"\[\s*0x[0-9A-Fa-f]{2}\s*(?:,\s*(?:0x[0-9A-Fa-f]{2}|\s)\s*)*\]",
                         text):
        out.append(tuple(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", m.group(0))))
    return out


def map_ids(text):
    ids = re.findall(r'\bbuild\(\s*["\']([^"\']+)["\']', text)
    ids += re.findall(r'(?m)^\s{4}["\']([\w-]+)["\']\s*:\s*\[', text)
    return sorted(ids)


def main(argv):
    dry = "--dry-run" in argv
    paths = [a for a in argv if not a.startswith("--")]
    edits = []
    for p in paths:
        edits.extend(json.loads(pathlib.Path(p).read_text(encoding="utf-8")))

    by_file = {}
    for e in edits:
        # agents report either a bare filename or an absolute path
        by_file.setdefault(pathlib.PurePath(e["file"]).name, []).append(e)

    applied = failed = 0
    for name, group in sorted(by_file.items()):
        f = DOCS / name
        if not f.exists():
            print(f"  {name}: MISSING")
            failed += len(group)
            continue
        before = f.read_text(encoding="utf-8")
        text = before
        ok = 0
        for e in group:
            n = text.count(e["old"])
            if n != 1:
                print(f"  {name}: {n} matches (need 1) for {e['old'][:64]!r}")
                failed += 1
                continue
            text = text.replace(e["old"], e["new"])
            ok += 1

        # the invariants: bytes and map ids may not move
        if byte_arrays(before) != byte_arrays(text):
            print(f"  {name}: REFUSED -- a byte value changed")
            failed += ok
            continue
        if map_ids(before) != map_ids(text):
            print(f"  {name}: REFUSED -- the set of byte maps changed")
            failed += ok
            continue

        if not dry and ok:
            f.write_text(text, encoding="utf-8")
        applied += ok
        delta = len(text) - len(before)
        print(f"  {name:26} {ok:3} edits  {delta:+6} bytes"
              + ("  (dry run)" if dry else ""))

    print(f"\n  {applied} applied, {failed} rejected")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
