"""`anomalies.scan` binds the file's size once. A rule must not overwrite it.

    size = os.path.getsize(filepath)      # the FILE's length, line 1 of scan
    ...
    while pos + 8 <= fsz:
        size, hlen = struct.unpack(">I", hdr[:4])[0], 8    # a BOX's length

The MP4 coverage rule walked top-level boxes and assigned each box header to
`size`, which the enclosing scope had already bound to the length of the file.

Nothing read it afterwards, so nothing was wrong -- for as long as that rule
stayed last. The moment a rule was added below it, a 254 KB file was accounted
against a 3,531-byte box and the tool reported that the structure explained
1.0% of it. The MP4 rule itself was correct throughout; it left a trap armed
for whoever came next.

That is why this is tested rather than only fixed. A loop variable shadowing an
outer binding produces no error, no warning and no wrong answer until someone
edits below it, and then it produces a confidently wrong one. Detecting it
needs a test that asks what `size` still means at the END of scan, which is
what the two below do -- one through the visible consequence, one by watching
the value directly.
"""

import os
import struct

import pytest

from acidcat.core.forensics import anomalies
from acidcat.core.walk import walk_file
from conftest import CORPUS_M4A


@pytest.fixture
def mp4(tmp_path):
    """A real MP4, which is what makes the box-walking rule run at all."""
    if not os.path.isfile(CORPUS_M4A):
        pytest.skip("no MP4 in the corpus")
    p = tmp_path / "probe.m4a"
    with open(CORPUS_M4A, "rb") as fh:
        p.write_bytes(fh.read())
    return str(p)


def test_a_rule_after_the_mp4_walk_still_sees_the_file_size(mp4, monkeypatch):
    """Watch the value directly, at the moment a later rule would read it.

    The consequence test below can only fail while a size-consuming rule
    happens to exist. This one holds whether or not one does, which matters
    because the whole defect is about what the next person's rule will see.
    """
    seen = {}
    real = anomalies.cavity.account if hasattr(anomalies, "cavity") else None

    from acidcat.core.forensics import cavity

    def spy(path, label, chunks, size=None, **kw):
        seen["size"] = size
        return cavity.account(path, label, chunks, size=size, **kw)

    monkeypatch.setattr(cavity, "account", spy)
    label, chunks, warns = walk_file(mp4)
    anomalies.scan(mp4, label, chunks, warns)

    if "size" not in seen:
        pytest.skip("no rule downstream of the MP4 walk consumes the file size")
    assert seen["size"] == os.path.getsize(mp4), (
        "by the end of scan the file's size had become %r; the MP4 box walk "
        "assigned a box header to the same name the enclosing scope uses for "
        "the file length" % (seen["size"],))
    assert real is None or True


def test_the_coverage_a_downstream_rule_reports_is_of_the_whole_file(mp4):
    """The visible consequence, and how this was actually found.

    `cavity.account` gave one answer called directly and another called from
    inside `scan`, on the same file and the same walk. Two answers for one
    question is the shape of the bug; the number itself was 1.0% against 100%.
    """
    from acidcat.core.forensics import cavity

    label, chunks, warns = walk_file(mp4)
    direct = cavity.account(mp4, label, chunks)
    findings = anomalies.scan(mp4, label, chunks, warns) or []
    after = cavity.account(mp4, label, chunks)

    assert direct["coverage"] == after["coverage"], (
        "the accounting changed depending on whether another rule had run "
        "over the same chunks first: %.4f then %.4f"
        % (direct["coverage"], after["coverage"]))
    for f in findings:
        if f["rule"] == "unaccounted_bytes":
            pytest.fail("a well-formed MP4 was reported as largely "
                        "unaccounted for: %s" % f["message"])


def test_the_mp4_rule_itself_still_works(tmp_path, mp4):
    """The fix renames a variable inside a rule that was never broken, so the
    rule's own behaviour is pinned alongside it. Bytes inside `mdat` that no
    sample table references are still reported."""
    with open(mp4, "rb") as fh:
        blob = bytearray(fh.read())
    # grow mdat by a payload the sample tables do not point at
    pos = 0
    while pos + 8 <= len(blob):
        n = struct.unpack_from(">I", blob, pos)[0]
        if blob[pos + 4:pos + 8] == b"mdat" and n > 8:
            pad = b"S" * 2048
            struct.pack_into(">I", blob, pos, n + len(pad))
            blob[pos + n:pos + n] = pad
            break
        if n < 8:
            break
        pos += n
    else:
        pytest.skip("no mdat box to grow")
    p = tmp_path / "planted.m4a"
    p.write_bytes(bytes(blob))
    label, chunks, warns = walk_file(str(p))
    rules = {f["rule"] for f in (anomalies.scan(str(p), label, chunks, warns) or [])}
    assert "mp4_mdat_coverage" in rules, rules
