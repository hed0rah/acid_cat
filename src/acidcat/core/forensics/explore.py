"""What is inside these bytes?

The one question a reverse-engineering tree asks, at every level, forever. A
node covers a byte range; something may live in it; that something covers a
range too. Nothing about the question changes with depth, so nothing about the
answer should either -- which is why this is one function rather than a special
case per level.

THE LADDER. Engines are alternatives, not contributors. The first one that
explains a range wins that range, and the others get their turn on the
CHILDREN, one level down:

  1. walk_file()  a real walker if one claims these bytes, and generic
                  structural triage if none does -- walk_file already tries
                  triage before raising, so this rung is one call.
  2. locate()     audio content found by signature and cadence, for a range
                  no walker claimed.

Ordering them and stopping at the first hit is what keeps the hierarchy. Run
`locate` on a range that a walker already explained and it sweeps straight
through the structure, reporting the innermost audio at its absolute offset and
skipping every layer in between -- which is exactly the flattening this tree
was built to stop. Measured: a blob holding a container holding two Ogg files
reported the two Oggs and never mentioned the container at all.

WHAT THIS CANNOT DO, stated because a tool that hides its blind spot is worse
than one that has it. Both rungs anchor at the START of the range: a walker
sniffs at offset 0, and triage needs its grid to begin there. `locate` is the
only one that finds things mid-range, and it knows audio magics only. So an
UNKNOWN container embedded at a nonzero offset among junk -- its magic in no
table, not filling its parent -- is invisible to this ladder. Recovering that
needs a sweep for plausible chunk-grid anchors at arbitrary offsets, which is a
detection engine with a false-positive budget rather than a rung, and is not
built. Such bytes render as unaccounted, which is what they are.
"""

from acidcat.core.forensics import locate as locatemod
from acidcat.core.infra import geometry
from acidcat.core.walk import walk_bytes
from acidcat.core.walk.base import Unsupported

# Nesting past this is a verdict about the file rather than a shortened answer:
# no real container is 32 deep, and a structure that says it is has stopped
# describing itself. Reported as a leaf when reached, never silently obeyed.
_MAX_DEPTH = 32

# Below this a chunk cannot hold a container: four bytes of magic and four of
# length is the smallest thing either rung can anchor on, and a range shorter
# than that has nothing for them to read. It governs whether an arrow is
# OFFERED, not what may be asked for -- exploring is always available on
# request, so this never refuses a question, it only declines to volunteer one.
_MIN_EXPLORABLE = 16


def explore(source, off, length, *, mode="normal", scratch_dir=None):
    """Look inside [off, off+length) of `source`. Never raises on content.

    Offsets in the result are ABSOLUTE in `source`, already rebased, because a
    child that reported its walker's view would point the hex pane at the wrong
    bytes -- silently, since both are valid offsets.
    """
    out = {"engine": None, "label": None, "chunks": [], "regions": [],
           "warnings": [], "note": None, "partial": False}
    if off is None or not length or length <= 0:
        return out

    try:
        with open(source, "rb") as f:
            f.seek(off)
            data = f.read(length)
    except OSError as e:
        out["warnings"].append(f"could not read these bytes: {e}")
        return out
    if not data:
        out["warnings"].append("no bytes to look at")
        return out
    if len(data) < length:
        out["partial"] = True
        out["note"] = (f"read {len(data):,} of the {length:,} bytes this range "
                       f"claims; the file ends first")

    # Rung 1 needs a path -- every walker does -- so `walk_bytes` carries the
    # carve-and-delete, in the one place that cost is measured and can later be
    # removed. Rungs below take bytes and need no file at all.
    try:
        label, chunks, warns = walk_bytes(data, deep=False,
                                          scratch_dir=scratch_dir)
    except Unsupported:
        chunks = None
    except Exception as e:                          # a walker bug is not fatal
        chunks = None
        out["warnings"].append(f"walk failed: {e.__class__.__name__}: {e}")
    if chunks:
        geometry.normalize(chunks, len(data))
        out.update(engine="walker", label=label,
                   chunks=_rebase_chunks(chunks, off))
        out["warnings"].extend(warns or [])
        return out

    try:
        regions = locatemod.locate(data, mode=mode)
    except Exception as e:
        out["warnings"].append(f"locate failed: {e.__class__.__name__}: {e}")
        return out
    if regions:
        out.update(engine="locate", label="located content",
                   regions=_rebase_regions(regions, off))
    return out


def _rebase_chunks(chunks, base):
    for c in chunks:
        for key in ("offset", "payload_base"):
            if isinstance(c.get(key), int):
                c[key] += base
    return chunks


def _rebase_regions(regions, base):
    for r in regions:
        for key in ("offset", "end"):
            if isinstance(r.get(key), int):
                r[key] += base
    return regions


def explorable(child, parent, depth):
    """May this child be opened further?

    `child` and `parent` are (offset, length) extents.

    A PROPER subrange, not merely a range. Equal extents are the obvious fixed
    point -- a walk that hands back its own input re-walks itself forever -- but
    so is any child that covers its parent, and a child that overflows its
    parent is describing something outside the thing it was found in.
    """
    if depth >= _MAX_DEPTH:
        return False
    coff, clen = child
    poff, plen = parent
    if coff is None or not clen or clen < _MIN_EXPLORABLE:
        return False
    if poff is None or not plen:
        return clen >= _MIN_EXPLORABLE
    if coff < poff or coff + clen > poff + plen:
        return False                      # not inside the thing it came from
    return clen < plen


def overflows(child, parent):
    """Does this child claim bytes outside its parent?

    Worth asking separately from `explorable`, because a chunk overrunning its
    container is a finding about the file rather than a rendering problem, and
    the two answers should not be confused with each other.
    """
    coff, clen = child
    poff, plen = parent
    if None in (coff, poff) or not clen or not plen:
        return False
    return coff < poff or coff + clen > poff + plen
