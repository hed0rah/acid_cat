"""What kind of thing is this file, and what should look at it next?

Three questions a reverse engineer asks before anything else: is this a single
file I understand, is it a bigger thing with files inside, or is it damaged
remains of either. This answers all three and names the verb that follows, so
an unknown file has an obvious next move instead of a dead end.

Built only on checks that measured cheap, on real corpora:

    magic at offset 0        0.08 ms/file   (97.5% of a real 3,229-file library)
    container signature sweep   76 ms/32 MB
    chunk-grid resync          1.2 s/32 MB
    statistical audio scan      13 s/32 MB   <- 174x the sweep; never run here

Deliberately NOT used: entropy variance across sampled windows as a
"container" signal. It reads well in theory and fails on real files -- a 286 MB
zip-based instrument library measures variance 0.000 (uniformly compressed,
indistinguishable from one big compressed file) while a small FLAC measures
1.563 (sampling artefact). Measured, rejected, and left out rather than shipped
as a plausible-sounding heuristic.
"""

import os

from acidcat.core.infra import sniff as sniffmod

# shapes this can return
SINGLE = "single"          # one file, a format we walk
CONTAINER = "container"    # a bigger thing with recognizable files inside
CHUNKED = "chunked"        # unknown format, but its chunk grid is readable
DAMAGED = "damaged"        # structure survives but the normal path cannot use it
OPAQUE = "opaque"          # no structure we can see; may still hold raw audio
FOREIGN = "foreign"        # identified, and not audio (a PDF in a sample pack)
EMPTY = "empty"

# formats that are containers by nature -- a disc image or sector dump holds
# files rather than being one
_CONTAINER_FORMATS = frozenset(("gcm", "wii", "cdxa", "cue", "n64rom", "snesrom"))

# things that turn up in sample libraries and are not audio. Naming them beats
# "unrecognized": 1.9% of a real corpus was this, mostly documents and art.
_FOREIGN_MAGICS = (
    (b"%PDF-", "PDF document"),
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF8", "GIF image"),
    (b"\x00\x05\x16\x07", "macOS AppleDouble resource fork"),
    (b"\x00\x05\x16\x00", "macOS AppleDouble resource fork"),
    (b"{\\rtf", "RTF document"),
    (b"\xd0\xcf\x11\xe0", "MS Office (OLE) document"),
    (b"\x7fELF", "ELF binary"),
    (b"MZ", "DOS/Windows executable"),
)

# compressed wrappers are NOT foreign: an Ableton Live Pack is a gzip stream and
# several instrument formats are zips, so what is inside may well be audio.
# Calling a .alp "not audio" would be wrong.
_COMPRESSED_MAGICS = (
    (b"PK\x03\x04", "zip archive"),
    (b"\x1f\x8b", "gzip stream"),
    (b"BZh", "bzip2 stream"),
    (b"\xfd7zXZ\x00", "xz stream"),
)


def _foreign(head):
    for magic, label in _FOREIGN_MAGICS:
        if head.startswith(magic):
            return label
    return None


def classify(path, *, deep=True):
    """Return a verdict dict for ``path``.

    keys: ``shape`` (one of the constants above), ``format`` (sniffed id or
    None), ``detail`` (a human sentence), ``next`` (the verb to run), and
    ``evidence`` (what the decision rested on).

    ``deep=False`` stops after the byte-cost-free checks -- magic and size --
    and never touches the container sweep or resync, for callers sorting a large
    tree where a per-file sweep would dominate.
    """
    size = os.path.getsize(path)
    if size == 0:
        return _verdict(EMPTY, None, "empty file", None, {"size": 0})
    with open(path, "rb") as f:
        head = f.read(64)

    fmt = sniffmod.sniff(path)
    ev = {"size": size, "sniff": fmt}

    # 1. a format we walk. Disc images and sector dumps are containers by nature.
    if fmt:
        if fmt in _CONTAINER_FORMATS:
            return _verdict(CONTAINER, fmt,
                            f"{fmt} image -- holds files rather than being one",
                            "extract", ev)
        return _verdict(SINGLE, fmt, f"{fmt}, a format acidcat walks",
                        "inspect", ev)

    # 2. identified, but not ours. Saying so beats "unrecognized".
    label = _foreign(head)
    if label:
        ev["identified_as"] = label
        return _verdict(FOREIGN, None, f"{label} -- not audio", None, ev)
    for magic, label in _COMPRESSED_MAGICS:
        if head.startswith(magic):
            ev["identified_as"] = label
            return _verdict(CONTAINER, None,
                            f"{label} -- compressed; what is inside may be audio",
                            "extract", ev)

    # 3. no magic we know, but is the structure readable anyway? Generic triage
    # walks an unknown chunk grid, and a surprising number of "unsupported"
    # formats are just IFF under another name -- a Reason .sxt is FORM/CAT/DESC
    # and walks today without anyone having written a walker for it.
    from acidcat.core.forensics import triage
    try:
        generic = triage.generic_walk(path)
    except Exception:
        generic = None
    if generic is not None:
        label, chunks, _warns = generic
        ev["triage_chunks"] = len(chunks)
        return _verdict(CHUNKED, None,
                        f"{label} -- {len(chunks)} chunk(s) readable without a "
                        f"format-specific walker", "inspect", ev)

    if not deep:
        return _verdict(OPAQUE, None, "no magic at offset 0", "od", ev)

    # 4. does it hold files? The signature sweep is 76 ms on 32 MB, so this is
    # affordable to ask of anything.
    data = _read_capped(path)
    from acidcat.core.forensics.locate import signature_sweep
    hits = signature_sweep(data)
    if hits:
        ev["embedded_containers"] = len(hits)
        ev["first_at"] = hits[0]["offset"]
        return _verdict(CONTAINER, None,
                        f"{len(hits)} audio container(s) embedded, first at "
                        f"0x{hits[0]['offset']:08x}", "locate", ev)

    # 5. is the structure damaged rather than absent? A chunk grid that chains
    # end-to-start is structure the normal path could not reach.
    from acidcat.core.forensics import resync
    rec = resync.recover(data, known_only=True)
    if rec["chain"]:
        ev["resync_chunks"] = len(rec["chain"])
        ev["resync_coverage"] = rec["coverage"]
        return _verdict(DAMAGED, None,
                        f"{len(rec['chain'])} chunk(s) recoverable by resync "
                        f"({rec['coverage']:.0%} of the file) -- the header is "
                        f"damaged but the grid survives", "inspect --resync", ev)

    # 6. nothing structural. It may still hold raw audio, which only the
    # statistical pass can find -- and that is expensive, so it is named, not run.
    return _verdict(OPAQUE, None,
                    "no magic, no embedded containers, no recoverable chunk grid",
                    "locate", ev)


_READ_CAP = 256 * 1024 * 1024


def _read_capped(path):
    with open(path, "rb") as f:
        return f.read(_READ_CAP)


def _verdict(shape, fmt, detail, nxt, evidence):
    return {"shape": shape, "format": fmt, "detail": detail,
            "next": nxt, "evidence": evidence}
