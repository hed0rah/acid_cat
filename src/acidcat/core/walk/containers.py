"""Containers: CUE sheets and GameCube disc images.

These hold other things rather than being a thing, which changes what a walk
should say. A stream format's walk describes its own bytes; a container's walk
describes what is inside it and where, because that is the only question anyone
opens one to ask.

Both readers here already existed. `extract` used them to pull audio off a disc
and then discarded the layout, so `inspect` said nothing about images acidcat
could already rip.

A CUE is the odd one: it is TEXT, so there is no chunk structure to report and
the walk is one region carrying the track table as fields. That is honest for
what a cue sheet is -- an index whose whole content is a list of positions in a
file it does not itself contain.
"""

import os

from acidcat.core.walk.base import _f

# Red Book: 75 sectors of audio per second.
_SECTORS_PER_SECOND = 75


def _msf(lba):
    """A sector count as minutes:seconds:frames, which is how a cue writes it."""
    m, rest = divmod(lba, 60 * _SECTORS_PER_SECOND)
    s, f = divmod(rest, _SECTORS_PER_SECOND)
    return "%02d:%02d:%02d" % (m, s, f)


def inspect_cue(filepath, deep=False):
    """A CUE sheet: the track table, and the file it indexes into.

    The positions are in SECTORS, not bytes, and they are relative to the
    binary the sheet names rather than to the sheet itself. So a cue is the one
    format here whose offsets point outside the file being walked, and saying
    which binary they point at matters more than any byte in the sheet.

    Nothing here is capped. `cue.parse` streams the sheet a line at a time, and
    a sheet is a track list -- a hundred lines for a full disc. An earlier draft
    carried a 4 MB cap and warned that it had "parsed the first 4 MB", which was
    never true of a parser that had already read every line.
    """
    from acidcat.core.containers import cue as cuemod
    size = os.path.getsize(filepath)
    warns = []
    try:
        tracks = cuemod.parse(filepath)
    except Exception as exc:
        return [{"id": "sheet", "offset": 0, "size": size,
                 "summary": "CUE sheet did not parse: %s" % exc,
                 "fields": [], "warnings": [], "payload_base": 0}], [str(exc)]

    # A track can carry no file at all: `parse` opens with cur_file = None, so a
    # sheet whose FILE line is damaged still yields TRACK entries that name
    # nothing. Filtering here rather than trusting every track to have a string
    # is the whole of the fix -- os.path.basename(None) raises.
    files = []
    unnamed = 0
    for t in tracks:
        name = t.get("file")
        if not name:
            unnamed += 1
        elif name not in files:
            files.append(name)
    audio = [t for t in tracks if str(t.get("type", "")).upper() == "AUDIO"]

    fields = [
        _f(0, 0, "tracks", len(tracks)),
        _f(0, 0, "audioTracks", len(audio),
           "the ones a ripper would take; the rest are data"),
        _f(0, 0, "binaries", len(files),
           "the sheet indexes into these; it contains no audio itself"),
    ]
    if unnamed:
        warns.append("%d track(s) name no file; the sheet's FILE line is "
                     "missing or damaged, so their positions index into "
                     "nothing" % unnamed)
    for name in files:
        here = os.path.join(os.path.dirname(os.path.abspath(filepath)),
                            os.path.basename(name))
        present = os.path.isfile(here)
        fields.append(_f(0, 0, "file", os.path.basename(name),
                         "present beside the sheet" if present
                         else "NOT found beside the sheet"))
        if not present:
            warns.append("the sheet names %r, which is not beside it; the "
                         "positions below index into a file that is absent"
                         % os.path.basename(name))

    for t in tracks:
        lba = t.get("start_lba", 0)
        fields.append(_f(0, 0, "track %02d" % t["num"],
                         "%s at %s" % (str(t.get("type", "?")).upper(), _msf(lba)),
                         "sector %s, %.1f s in" % (format(lba, ","),
                                                   lba / float(_SECTORS_PER_SECOND))))

    kinds = sorted({str(t.get("type", "?")).upper() for t in tracks})
    return [{"id": "sheet", "offset": 0, "size": size,
             "summary": "CUE sheet, %d track(s) (%s) across %d binary file(s)"
                        % (len(tracks), ", ".join(kinds), len(files)),
             "fields": fields, "warnings": [], "payload_base": 0}], warns


def inspect_gcm(filepath, deep=False):
    """A GameCube disc image: the header, then the file table it points at.

    Only the header and the file-system table are read. The image itself is
    gigabytes and none of it is loaded, so what follows is a description of the
    disc's index rather than of its contents.
    """
    import struct
    from acidcat.core.containers import gcm
    size = os.path.getsize(filepath)
    warns = []
    with open(filepath, "rb") as fh:
        head = fh.read(0x440)
    if len(head) < 0x440:
        return [{"id": "header", "offset": 0, "size": size,
                 "summary": "GameCube image header is truncated",
                 "fields": [], "warnings": [], "payload_base": 0}], \
               ["file ends inside the 0x440-byte disc header"]

    game_id = head[0:6].decode("latin-1", "replace")
    maker = head[4:6].decode("latin-1", "replace")
    name = head[0x20:0x60].split(b"\x00")[0].decode("latin-1", "replace").strip()
    dol_off, fst_off, fst_size = struct.unpack_from(">III", head, 0x420)

    entries = list(gcm.walk(filepath))
    audio_ext = (".adx", ".dsp", ".hps", ".ast", ".afc", ".brstm", ".thp")
    tunes = [e for e in entries
             if str(e.get("path", "")).lower().endswith(audio_ext)]

    fields = [
        _f(0x00, 6, "gameId", game_id, "four-character code plus the maker"),
        _f(0x04, 2, "maker", maker),
        _f(0x20, 0x40, "internalName", name or "(unnamed)"),
        _f(0x420, 4, "dolOffset", "0x%08X" % dol_off,
           "the main executable", enc=">I", raw=dol_off, xref=dol_off),
        _f(0x424, 4, "fstOffset", "0x%08X" % fst_off,
           "the file-system table", enc=">I", raw=fst_off, xref=fst_off),
        _f(0x428, 4, "fstSize", format(fst_size, ","), enc=">I", raw=fst_size),
        _f(0, 0, "files", format(len(entries), ","),
           "read from the FST; the image itself is not loaded"),
        _f(0, 0, "audioFiles", format(len(tunes), ","),
           "by extension: %s" % ", ".join(audio_ext)),
    ]
    if fst_off >= size:
        warns.append("the file-system table is declared past the end of the image")
    if not entries:
        warns.append("no files could be read from the file-system table")

    for e in tunes[:12] if not deep else tunes:
        fields.append(_f(0, 0, os.path.basename(str(e.get("path", "?"))),
                         "%s bytes" % format(e.get("size", 0), ","),
                         "at 0x%08X" % e.get("offset", 0)))
    if len(tunes) > 12 and not deep:
        fields.append(_f(0, 0, "more", "%d further audio file(s)" % (len(tunes) - 12),
                         "shown with deep inspection"))

    return [{"id": "header", "offset": 0, "size": min(0x440, size),
             "summary": "GameCube disc %s%s, %s file(s), %d audio"
                        % (game_id, (" -- %s" % name) if name else "",
                           format(len(entries), ","), len(tunes)),
             "fields": fields, "warnings": [], "payload_base": 0},
            {"id": "image", "offset": min(0x440, size),
             "size": max(0, size - 0x440),
             "summary": "%s bytes of disc data, indexed by the FST above"
                        % format(max(0, size - 0x440), ","),
             "fields": [], "warnings": [], "payload_base": min(0x440, size)}], warns
