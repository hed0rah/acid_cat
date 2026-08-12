"""acidcat TUI -- the AcidcatTUI application class.

The interactive shell: the tree/hex/detail panes, key bindings, and every action
(walk, scan, carve, extract, in-place hex edit, metadata edit, validate). The
modal screens live in screens.py; byte/field rendering and the edit profiles in
render.py.
"""
import os
import re
import shutil
import struct
import tempfile

from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Input, Static, Tree

from acidcat.core.walk import walk_file, Unsupported
from acidcat.core.forensics import anomalies as ac_anom
from acidcat.core.forensics import locate as locatemod
from acidcat.core.forensics import transforms as transformsmod
from acidcat.core.codecs import cdxa as cdxamod
from acidcat.core.codecs import vag as vagmod
from acidcat.core.containers import iso9660 as isomod
from acidcat.core.forensics import audioscan as audioscanmod
from acidcat.core.write import writer
from acidcat.core.forensics import viz
from acidcat.commands.carve import _EXT as _CARVE_EXT
from acidcat.util import play
from acidcat.core.write.edits import EditError
from acidcat.commands.write import _edit as _write_edit, _strip as _write_strip

# the field value<->bytes engine (struct inference, named codecs, the three
# bit-field encodings) lives in core/infra/fieldcodec.py so the CLI and tests
# share it without a textual dependency.
from acidcat.core.infra.fieldcodec import (
    _BE_FMTS, _BITMAPS, _DYNMAPS, _field_abs, _resolve_in_map,
    bitfield_apply, bitfield_extract, decode_value, enc_size, encode_value,
    infer_enc, parse_bitfield, parse_bitsdyn, parse_bitsmap, resolve_bitsmap,
)


# brand theme (ink / gunmetal + teal/orange accents); source of truth is
# acidcat/tui_theme.py, imported by the playground TUI too so they cannot drift.
from acidcat.tui_theme import (
    PALETTE, ACCENT, FG, SOFT, DIM, GUTTER, PEND, AMBER, SEV, TEAL, byte_color,
    ramp_color,
)

from acidcat.tui_app.render import (
    edit_profile, hex_text, text_field_for, _read, _fuzzy, _hex_rows,
    row_width_for, trim_size_echo,
    _SPIN, _BAR_W, _HEX_CAP, _ROW_CAP, _CHUNK_CAP, _HEXEDIT_CAP, _UNDO_CAP, _VIZ_READ,
    _SEARCH_CAP,
    _UNDO_BYTES_CAP, _DIFF_CAP, _LARGE_FILE, _SCAN_SEG,
)
from acidcat.tui_app.screens import (
    BrowseScreen, ConfirmScreen, DiffScreen, DiscScreen, EditScreen, HelpScreen,
    YesNoScreen,
    HexPane, MapScreen, PromptScreen, RegionsScreen, ValidateScreen,
)


class AcidcatTUI(App):
    CSS = """
    Screen { background: #16181C; }
    #left { width: 50%; }
    #right { width: 50%; }
    /* The left column's top box, deliberately the same fixed height as
       #detail on the right so the tree and the hex pane start on the same
       row. Four content lines: what the file is, what forensics found, and
       room for whatever else belongs in a quick readout. It scrolls, so a
       genuinely damaged file's longer list stays reachable without the box
       resizing and shifting everything below it. */
    #idbox { height: 6; border: round #3A3E45; }
    #idbox.findings { border: round #FF4D00; }
    #title { height: auto; padding: 0 1; }
    #anom { height: auto; padding: 0 1; }
    #tree { border: round #08F9DF; padding: 0 1; }
    /* Fixed, not auto, and the same height as #idbox opposite it. On auto it
       grew when a summary was long enough to wrap, stealing rows from the hex
       pane and making the layout jump as you moved through the tree. Four
       content rows always, and a long line clips. */
    #detail { height: 6; border: round #3A3E45; padding: 0 1; color: #C9CDD3; }
    #hexwrap { border: round #08F9DF; }
    #hex { padding: 0 1; }
    #editbar { dock: bottom; height: 3; border: round #FF4D00; background: #16181C; }
    #editbar.hidden { display: none; }

    /* Zoom. A hex row is 76 columns wide and the right pane is 52% of the
       terminal, so below about 154 columns the grid folds -- at 120 the pane
       is 61 wide. Textual can maximize natively, but only for widgets whose
       read-only `allow_maximize` is true, which excludes these containers, and
       it postdates the `textual>=0.60` floor in pyproject. Classes work on
       every version and need no feature test. */
    Screen.zoom-hex #left, Screen.zoom-hex #detail { display: none; }
    Screen.zoom-hex #right { width: 100%; }
    Screen.zoom-tree #right { display: none; }
    Screen.zoom-tree #left { width: 100%; }
    Tree { background: #16181C; }
    Tree > .tree--guides { color: #3A3E45; }
    Tree > .tree--guides-selected { color: #08F9DF; }
    """

    # Every binding used to carry show=True, so the footer listed all of them and
    # the ones a user reaches for most -- edit, in particular -- were pushed off
    # the end. The footer now carries the common path; `?` lists everything.
    BINDINGS = [
        ("q", "request_quit", "quit"),
        ("g", "goto", "goto offset"),
        ("slash", "search", "search"),
        Binding("n", "search_next", "next match", show=False),
        Binding("N", "search_prev", "prev match", show=False),
        Binding("f", "next_finding", "next finding", show=False),
        ("x", "follow_xref", "follow"),
        Binding("y", "yank", "yank hex", show=False),
        Binding("d", "diff", "pending changes", show=False),
        ("m", "map", "byte map"),
        Binding("b", "cycle_view", "byte view", show=False),
        Binding("r", "viz_scope", "viz: file/region", show=False),
        Binding("S", "viz_scale", "viz: scale", show=False),
        ("p", "play", "play"),
        ("full_stop", "stop_play", "stop"),
        Binding("v", "validate", "validate", show=False),
        ("ctrl+s", "save", "save"),
        Binding("ctrl+z", "undo", "undo", show=False),
        Binding("ctrl+r", "redo", "redo", show=False),
        Binding("o", "open", "open file", show=False),
        Binding("l", "locate_regions", "locate regions", show=False),
        Binding("u", "ascend", "up to regions", show=False),
        ("e", "edit_field", "edit field"),
        # ctrl+e, not tab: tab moves focus, and entering an edit mode should
        # take a modifier. Priority because Textual binds ctrl+e at the screen
        # level in some versions.
        Binding("ctrl+e", "hex_focus", "hex edit", show=False, priority=True),
        Binding("ctrl+t", "toggle_mode", "value/hex", show=False),
        Binding("w", "edit", "edit tags", show=False),
        Binding("s", "strip", "strip meta", show=False),
        ("z", "zoom", "zoom pane"),
        # priority: Textual binds tab/shift+tab to focus_next/focus_previous at
        # the screen level and consumes them first, walking every focusable
        # widget in DOM order rather than pane to pane.
        Binding("tab", "focus_pane", "focus pane", show=False, priority=True),
        Binding("shift+tab", "focus_pane_back", "focus pane back",
                show=False, priority=True),
        ("a", "expand_all", "expand"),
        ("c", "collapse_all", "collapse"),
        ("question_mark", "help", "help"),
        Binding("escape", "cancel_edit", "cancel edit", show=False),
        Binding("plus", "more_rows", "show more rows", show=False),
        Binding("equals_sign", "more_rows", "show more rows", show=False),
        # scan controls: priority so they beat the tree's own space/enter while a
        # scan runs; check_action keeps them dormant otherwise.
        Binding("space", "pause_scan", "pause scan", priority=True, show=False),
        Binding("enter", "keep_scan", "keep scan", priority=True, show=False),
    ]

    def check_action(self, action, parameters):
        if action in ("pause_scan", "keep_scan"):
            return self._scanning        # only live during a scan
        # while a modal (edit form / file browser / help / diff / map / confirm)
        # is open, the app-global single-letter bindings must not fire under it
        # -- so typing in the browser or a form does not trigger edit/strip/etc.
        if len(self.screen_stack) > 1:
            return False
        return True

    def __init__(self, path=None):
        super().__init__()
        self.src = path           # the file being edited (save target + display name)
        self.work = None          # temp working copy: edits land here until save
        self.dirty = False        # unsaved edits present
        self._backed_up = False   # a _original backup was made this session
        self.fsize = 0
        self.chunks = []
        self.fmt = "?"
        self.warns = []
        self.findings = []
        self._rowbudget = {}      # chunk index -> how many of its rows to list
        self._morerows = {}       # id(node) -> chunk index, for the "more rows" node
        self._morechunks = set()  # id(node) for the "more chunks" node
        self._nodemeta = {}       # id(node) -> (off, length, accent)  for the hex pane
        self._nodekey = {}        # id(node) -> stable key, to restore the cursor
                                  # and expansion across the post-edit tree rebuild
        self._editval = {}        # id(node) -> (value, enc, raw)  for field nodes
        self._textfield = {}      # id(node) -> engine field  for variable-length text
        self._profile = None      # edit profile of the current file (WAV/AIFF/...)
        self._prefer_be = False   # format is big-endian: bias infer_enc that way
        self._cur_node = None     # last highlighted tree node
        self._edit_target = None  # active inline edit: dict(off,length,name,mode,fmt,accent)
        self._hexedit = None      # active in-pane hex edit: dict(off,length,buf,cur,nib)
        self._undo = []           # working-copy byte snapshots for undo
        self._redo = []           # snapshots popped by undo, for redo
        self._prompt = None       # active editbar prompt: dict(kind, ...)
        self._allnodes = []       # (node, off, length) for offset/fuzzy navigation
        self._search = None       # active search: dict(desc, hits, idx)
        self._finding_idx = -1    # cursor into self.findings for jump-to-finding
        self._xref = {}           # id(field node) -> absolute target offset (pointer)
        self._view = "hex"        # byte-view mode: hex | entropy | hilbert | histogram
        self._viz_scope = "file"  # what the byte views cover: file | region (r)
        self._viz_scale = {}      # view mode -> index into _VIZ_SCALES (S)
        self._zoom = None         # which pane owns the screen, cycled by z
        self._cur_region = (None, None, ACCENT)  # last shown (off, length, accent)
        self._cur_spans = None    # field spans for per-field hex tint (chunk view)
        self._play = None         # handle to a running audio-audition process
        self._regions = None      # locate regions of the blob being browsed, or None
        self._blob_src = None     # path of the blob those regions came from
        self._region_view = None  # (idx, region) when viewing a descended region
        self._region_tmps = []    # carved-region temp files, cleaned on exit
        self._disc_src = None     # path of an opened CD-XA disc image
        self._disc_list = []      # its audio catalog (.STR/.VB/.VAG entries)
        self._locate_mode = "normal"    # strict | normal | aggressive
        self._locate_transforms = False  # also run the XOR/rotate/nibble lens
        self._readonly = False    # a large file is browsed in place (no working copy)
        self._work_is_temp = False  # self.work is a temp we own and must clean up
        self._scanning = False    # a region scan is running in the worker
        self._cancel_scan = False  # set to break the scan loop (keep or discard)
        self._scan_paused = False  # space: freeze the scan at the current segment
        self._scan_discard = False  # esc chose discard (vs enter = keep partial)
        self._scan_partial = False  # last scan was kept early (results are partial)
        self._scan_frame = 0      # spinner phase
        self._scan_last = None    # (done, total, n) for the spinner re-render
        self._scan_timer = None   # set_interval handle animating the spinner

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="left"):
                with VerticalScroll(id="idbox"):
                    yield Static(id="title")
                    yield Static(id="anom")
                yield Tree("file", id="tree")
            with Vertical(id="right"):
                yield Static(id="detail")
                with VerticalScroll(id="hexwrap"):
                    yield HexPane(id="hex")
        yield Input(id="editbar", classes="hidden")
        yield Footer()

    def on_mount(self):
        if self.src:
            self._open_path(self.src)
        else:
            self.query_one("#title", Static).update(
                Text(" acidcat tui   press o to open a file",
                     style=f"bold {ACCENT}"))
            self.action_open()

    def on_unmount(self):
        self.action_stop_play(quiet=True)
        self._discard_work()
        self._clean_region_tmps()

    # ── working copy: all edits apply to a temp file until an explicit save ──

    def _open_path(self, path):
        """Open `path` fresh: drop any region context, make a working copy, load,
        and offer the region browser if it is a blob rather than a single file."""
        self.src = path
        self._regions = None
        self._blob_src = None
        self._region_view = None
        self._clean_region_tmps()
        self._make_work()
        self._load()
        # the hidden #editbar is still focusable, so without this every single-key
        # binding (and the arrows) goes into it instead of the tree -- same reason
        # the scan path focuses the tree explicitly
        self.query_one("#tree", Tree).focus()
        if self._maybe_disc():          # a CD-XA disc opens straight into audio browsing
            return
        self._maybe_regions()

    # ── blob region browsing: locate -> browse -> descend -> extract ──────────

    def _clean_region_tmps(self):
        for p in getattr(self, "_region_tmps", []):
            try:
                if os.path.isfile(p):
                    os.unlink(p)
            except OSError:
                pass
        self._region_tmps = []

    def _maybe_regions(self):
        """Auto-offer the region browser when the opened file is not a single
        recognized format (a disk image, a raw dump, an unknown blob)."""
        if self.fmt not in ("unsupported", "walk failed") or self.fsize < 4096:
            return
        self.action_locate_regions()

    # ── CD-XA disc audio: detect -> browse tracks/banks -> audition/extract ──

    def _maybe_disc(self):
        """If the file is a CD-XA disc image with named audio, open the disc
        audio browser instead of the generic region browser. Returns True if so."""
        try:
            info = cdxamod.detect_cd_image(self.src)
        except Exception:
            return False
        if not info or not info.get("xa"):
            return False
        entries = self._disc_entries(self.src)
        if not entries:
            return False
        self._disc_src = self.src
        self._disc_list = entries
        self.push_screen(DiscScreen(entries, os.path.basename(self.src)))
        return True

    @staticmethod
    def _disc_entries(path):
        """The disc's audio catalog from its ISO 9660 tree: .STR/.XA soundtrack
        and .VB/.VAG SPU sound banks."""
        entries = []
        try:
            for ent in isomod.walk(path):
                up = ent["path"].upper()
                kind = ("XA" if up.endswith((".STR", ".XA"))
                        else "VB" if up.endswith(".VB")
                        else "VAG" if up.endswith(".VAG") else None)
                if kind:
                    entries.append({**ent, "kind": kind})
        except Exception:
            return []
        return entries

    def _decode_entry(self, ent, preview=False):
        """Decode a disc audio entry to (pcm_bytes, info). `preview` caps the
        length for a fast audition. Runs off the UI thread. None on failure."""
        path = self._disc_src
        try:
            if ent["kind"] == "XA":
                count = (ent["size"] + 2047) // 2048
                return cdxamod.decode_range(path, ent["lba"], count,
                                            max_audio=180 if preview else None)
            raw = isomod.read_file(path, ent)
            if ent["kind"] == "VB":
                if preview:
                    raw = raw[:24 * 1024]
                pcm = vagmod.decode_spu(raw, stop_on_end=False)
                return pcm, {"channels": 1, "rate": 22050, "bits": 16}
            info = vagmod.parse_vag(raw)                  # VAG
            data = info["data"][:24 * 1024] if preview else info["data"]
            return vagmod.decode_spu(data), {"channels": 1, "rate": info["rate"], "bits": 16}
        except Exception:
            return None

    def _audition_disc(self, ent):
        if not play.have_audio():
            self.notify("no audio player found (install ffmpeg for ffplay)",
                        severity="warning")
            return
        self.notify(f"decoding {ent['path']} ...")
        self.run_worker(lambda: self._audition_work(ent), thread=True)

    def _audition_work(self, ent):
        r = self._decode_entry(ent, preview=True)
        if r:
            self.call_from_thread(self._play_pcm, r[0], r[1], ent["path"])
        else:
            self.call_from_thread(self.notify, f"could not decode {ent['path']}",
                                  severity="warning")

    def _play_pcm(self, pcm, info, label):
        self.action_stop_play(quiet=True)
        self._play = play.play_bytes(pcm, rate=info["rate"], ch=info["channels"],
                                     bits=16, floating=False)
        secs = len(pcm) / max(1, info["rate"] * info["channels"] * 2)
        self.notify(f"playing {label}  ~{secs:.0f}s (preview) -- . to stop")

    def _extract_disc(self, entries):
        default = os.path.join(os.path.dirname(os.path.abspath(self._disc_src)),
                               os.path.splitext(os.path.basename(self._disc_src))[0]
                               + "_audio")
        self.push_screen(
            PromptScreen(f"extract {len(entries)} audio file(s) to (enter to confirm):",
                         default),
            lambda d: self._do_extract_disc(entries, d))

    def _do_extract_disc(self, entries, outdir):
        if not outdir:
            return
        self.notify(f"extracting {len(entries)} file(s) to {outdir} ...")
        self.run_worker(lambda: self._extract_disc_work(entries, outdir), thread=True)

    def _extract_disc_work(self, entries, outdir):
        from acidcat.core.primitives.wavio import pcm_wav
        try:
            os.makedirs(outdir, exist_ok=True)
            n = 0
            for ent in entries:
                r = self._decode_entry(ent, preview=False)
                if not r:
                    continue
                pcm, info = r
                base = ent["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
                with open(os.path.join(outdir, f"{n:04d}_{base}.wav"), "wb") as f:
                    f.write(pcm_wav(pcm, info["rate"], info["channels"]))
                n += 1
            self.call_from_thread(self.notify, f"extracted {n} file(s) -> {outdir}")
        except Exception as e:
            self.call_from_thread(self.notify, f"extract failed: {e}", severity="error")

    def action_locate_regions(self, reset_blob=True):
        """Run `locate` over the blob and open the region browser. Works on demand
        (the `l` key on any file) and automatically for a blob; reset_blob=False
        keeps the current blob when re-scanning with a new mode / the lens."""
        if len(self.screen_stack) > 1 or self._scanning:
            return
        if reset_blob:
            self._blob_src = self.src
        self._scanning = True
        self._cancel_scan = False
        self._scan_paused = False
        self._scan_discard = False
        self._scan_frame = 0
        try:
            total0 = os.path.getsize(self._blob_src)
        except OSError:
            total0 = 0
        self._scan_last = (0, total0, 0)                 # a bar at 0% before segment 1
        self._render_scan_title()
        try:                                             # so space/enter reach the
            self.query_one("#tree", Tree).focus()        # scan-control priority binds,
        except Exception:                                # not the hidden editbar Input
            pass
        self._scan_timer = self.set_interval(0.1, self._spin_scan)
        self.run_worker(self._locate_work, thread=True, exclusive=True)

    def _locate_work(self):
        """Scan the blob in segments so progress is visible and a scan can be
        paused, kept early, or discarded. Segments are mmap'd, so the image is
        never read whole into RAM."""
        import mmap
        import time
        regions = []
        try:
            with open(self._blob_src, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                if size == 0:
                    self.call_from_thread(self._finish_scan, [])
                    return
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    pos = 0
                    while pos < size and not self._cancel_scan:
                        while self._scan_paused and not self._cancel_scan:
                            time.sleep(0.08)             # space froze it; hold here
                        if self._cancel_scan:
                            break
                        seg = mm[pos:pos + _SCAN_SEG]     # one segment, not the whole file
                        segs = locatemod.locate(seg, mode=self._locate_mode)
                        if self._locate_transforms:
                            segs = segs + transformsmod.find_transformed_audio(seg)
                        for r in segs:                   # shift into absolute offsets
                            r["offset"] += pos
                            r["end"] += pos
                        regions.extend(segs)
                        pos += _SCAN_SEG
                        self.call_from_thread(self._scan_progress,
                                              min(pos, size), size, len(regions))
                finally:
                    mm.close()
        except Exception:
            pass
        cancelled = bool(self._cancel_scan)
        regions = self._merge_boundary(sorted(regions, key=lambda r: r["offset"]))
        self.call_from_thread(self._finish_scan, regions, cancelled)

    def _scan_progress(self, done, total, n):
        if not self._scanning:
            return
        self._scan_last = (done, total, n)
        self._render_scan_title()

    def _spin_scan(self):
        """Interval tick: advance the spinner so the status bar stays alive even
        between (coarse) segment completions."""
        if not self._scanning:
            return
        if not self._scan_paused:
            self._scan_frame += 1
        self._render_scan_title()

    def _render_scan_title(self):
        if self._scan_last is None:
            return
        try:
            title = self.query_one("#title", Static)
        except Exception:
            return
        title.update(self._scan_title(*self._scan_last))

    @staticmethod
    def _cap(t, key, label, label_style=SOFT):
        """A keycap: dim brackets, bright key, muted label -- the anti-style bit."""
        t.append(" [", style=DIM)
        t.append(key, style=f"bold {ACCENT}")
        t.append("]", style=DIM)
        t.append(f" {label} ", style=label_style)

    def _scan_title(self, done, total, n):
        pct = done * 100 // total if total else 0
        fill = pct * _BAR_W // 100
        t = Text()
        if self._scan_paused:
            t.append(" ⏸ ", style=f"bold {AMBER}")     # pause glyph
            t.append("paused", style=f"bold {AMBER}")
        else:
            t.append(f" {_SPIN[self._scan_frame % len(_SPIN)]} ", style=f"bold {ACCENT}")
            t.append("scanning", style=f"bold {ACCENT}")
        t.append("  ")
        t.append("█" * fill, style=ACCENT)              # filled bar
        t.append("░" * (_BAR_W - fill), style=GUTTER)   # empty bar
        t.append(f" {pct:>3}%", style=f"bold {FG}")
        t.append(f"   {done >> 20}/{total >> 20} MB", style=SOFT)
        t.append(f"   {n} region{'' if n == 1 else 's'}", style=SOFT)
        t.append(f"   {self._locate_mode}", style=DIM)
        if self._locate_transforms:
            t.append(" +lens", style=DIM)
        t.append("  ")
        self._cap(t, "space", "resume" if self._scan_paused else "pause")
        self._cap(t, "enter", "keep")
        self._cap(t, "esc", "discard", label_style=AMBER)
        return t

    @staticmethod
    def _merge_boundary(regions):
        """Heal a blob that got split across a segment boundary: coalesce exactly
        adjacent regions of the same kind/format."""
        if not regions:
            return regions
        out = [dict(regions[0])]
        for r in regions[1:]:
            prev = out[-1]
            same_kind = (r["kind"] == prev["kind"]
                         and r.get("format") == prev.get("format"))
            # A container whose extent was clipped at a segment boundary carries
            # corrupt_extent, and the bytes that follow are its own payload --
            # which the statistical pass now reports as blobs. They are neither
            # the same kind nor absorbable (the container's end stops at the
            # boundary), so the container has to swallow them as it goes.
            continues = (prev["kind"] == "container" and r["kind"] == "blob"
                         and prev.get("corrupt_extent")
                         and r["offset"] <= prev["end"])
            if r["offset"] == prev["end"] and (same_kind or continues):
                prev["end"] = r["end"]
                prev["length"] = prev["end"] - prev["offset"]
            elif continues:
                prev["end"] = max(prev["end"], r["end"])
                prev["length"] = prev["end"] - prev["offset"]
            else:
                out.append(dict(r))

        # Then drop statistical hits that are really a container's own payload.
        # `locate` does this over a whole file, but a segmented scan never sees
        # a whole file: a 12 KB WAV spans three 4 KB segments, so the first
        # yields the container and the rest yield its PCM as separate blobs.
        # They are not adjacent AND not the same kind, so the coalesce above
        # cannot join them -- the containers have to absorb them instead.
        from acidcat.core.forensics.locate import _mostly_within
        extents = [(r["offset"], r["end"]) for r in out
                   if r["kind"] == "container" and r["end"] > r["offset"]]
        if not extents:
            return out
        return [r for r in out
                if r["kind"] == "container"
                or not _mostly_within(r["offset"], r["end"], extents)]

    def action_pause_scan(self):
        """space: freeze the scan at the current segment, or resume it."""
        if not self._scanning:
            return
        self._scan_paused = not self._scan_paused
        self._render_scan_title()

    def action_keep_scan(self):
        """enter: stop the scan now and browse whatever was found so far."""
        if self._scanning:
            self._scan_paused = False
            self._scan_discard = False
            self._cancel_scan = True

    def action_cancel_scan(self):
        """esc: abandon the scan and discard the partial results."""
        if self._scanning:
            self._scan_paused = False
            self._scan_discard = True
            self._cancel_scan = True

    def _finish_scan(self, regions, cancelled=False):
        if self._scan_timer is not None:
            self._scan_timer.stop()
            self._scan_timer = None
        self._scanning = False
        self._cancel_scan = False
        self._scan_paused = False
        self._scan_last = None
        discard = self._scan_discard
        self._scan_discard = False
        if discard:                       # esc: drop everything, back to the file
            self._scan_partial = False
            self._load()
            return
        self._scan_partial = cancelled    # enter mid-scan: results are partial
        self._show_regions(regions)

    def _classify_regions(self, regions):
        """Rank each blob region -- codec (SPU-ADPCM) vs linear PCM at a geometry
        -- so the browser shows the real interpretation, not a bare 'raw-pcm'."""
        for r in regions:
            if r.get("kind") != "blob" or r.get("format"):
                continue
            try:
                with open(self._blob_src, "rb") as f:
                    f.seek(r["offset"])
                    data = f.read(min(r["end"] - r["offset"], 32768))
                r["probe"] = audioscanmod.classify(data)
            except Exception:
                pass

    def _show_regions(self, regions):
        self._load()                                   # restore the title
        self._classify_regions(regions)
        self._regions = regions
        if not regions:
            self.query_one("#title", Static).update(
                Text(f" {os.path.basename(self.src)}  --  no audio regions located "
                     f"[mode:{self._locate_mode}"
                     f"{'  lens:ON' if self._locate_transforms else ''}]  "
                     "(m mode  t lens  c carve  / search  l rescan)",
                     style=f"bold {ACCENT}"))
            return
        name = os.path.basename(self._blob_src)
        if self._scan_partial:
            name += " (partial -- scan stopped)"
        self.push_screen(
            RegionsScreen(regions, name, self._locate_mode, self._locate_transforms),
            self._on_region_action)

    def _on_region_action(self, result):
        if not result:
            return
        act = result["action"]
        if act == "descend":
            self._descend(result["index"])
        elif act == "extract":
            self._extract([self._regions[result["index"]]])
        elif act == "extract_all":
            self._extract(self._regions)
        elif act == "rescan":
            self._locate_mode = result["mode"]
            self._locate_transforms = result["transforms"]
            self.action_locate_regions(reset_blob=False)
        elif act == "carve":
            self._carve_prompt()
        elif act == "search":
            self._search_prompt()

    def _region_bytes(self, region):
        with open(self._blob_src, "rb") as f:
            f.seek(region["offset"])
            return f.read(region["end"] - region["offset"])

    def _descend(self, idx):
        self._descend_region(self._regions[idx], idx)

    def _descend_region(self, region, label):
        """Carve `region` to a temp file and open it as if it were a standalone
        file; the parent blob's regions stay set so `u` ascends."""
        ext = _CARVE_EXT.get(region.get("format")) or "bin"
        fd, tmp = tempfile.mkstemp(suffix=f".{ext}", prefix="acidcat_region_")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(self._region_bytes(region))
        self._region_tmps.append(tmp)
        blob, regions = self._blob_src, self._regions   # preserve across _make_work
        self.src = tmp
        self._region_view = (label, region)
        self._make_work()
        self._load()
        self._blob_src, self._regions = blob, regions

    def action_ascend(self):
        """From a descended region, go back up to the blob's region browser."""
        if self._regions is None or self._blob_src is None:
            self.notify("not inside a region -- u returns from a region opened "
                        "with l or from a blob")
            return
        self._region_view = None
        self.push_screen(
            RegionsScreen(self._regions, os.path.basename(self._blob_src),
                          self._locate_mode, self._locate_transforms),
            self._on_region_action)

    # ── RE tools inside the browser: manual carve + raw-byte search ───────────

    def _carve_prompt(self):
        self.push_screen(
            PromptScreen("carve range -- offset length (hex ok, e.g. 0x4a00 0x800):", ""),
            self._do_carve)

    def _do_carve(self, spec):
        if not spec:
            return
        try:
            parts = spec.replace(",", " ").split()
            off = int(parts[0], 0)
            length = int(parts[1], 0) if len(parts) > 1 else None
            end = off + length if length is not None else os.path.getsize(self._blob_src)
        except (ValueError, IndexError):
            self.query_one("#title", Static).update(
                Text(" carve: need 'offset length' (0x.. or decimal)",
                     style=f"bold {SEV['alert']}"))
            return
        blob_size = os.path.getsize(self._blob_src)
        off = max(0, min(off, blob_size))
        end = max(off, min(end, blob_size))
        self._descend_region(
            {"offset": off, "end": end, "length": end - off,
             "kind": "carve", "format": None, "confidence": 1.0}, "manual carve")

    def _search_prompt(self):
        self.push_screen(
            PromptScreen('byte search -- 0x48454C4C hex, or "text" ascii:', ""),
            self._do_search)

    def _do_search(self, pat):
        if not pat:
            return
        needle = self._parse_needle(pat)
        if not needle:
            self.query_one("#title", Static).update(
                Text(" search: give hex (0x..) or a \"quoted\" string",
                     style=f"bold {SEV['alert']}"))
            return
        import mmap
        try:
            size = os.path.getsize(self._blob_src)
            with open(self._blob_src, "rb") as f:
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    hit = mm.find(needle)                    # scans the image, no full read
                finally:
                    mm.close()
        except (OSError, ValueError):
            return
        if hit < 0:
            self.query_one("#title", Static).update(
                Text(f" search: {pat!r} not found", style=f"bold {ACCENT}"))
            self.action_locate_regions(reset_blob=False)   # reopen the browser
            return
        end = min(hit + max(len(needle), 1 << 20), size)   # a 1 MB window from the hit
        self._descend_region(
            {"offset": hit, "end": end, "length": end - hit,
             "kind": "match", "format": None, "confidence": 1.0},
            f"search @ 0x{hit:08x}")

    @staticmethod
    def _parse_needle(pat):
        pat = pat.strip()
        if len(pat) >= 2 and pat[0] == pat[-1] == '"':
            return pat[1:-1].encode("latin-1", "replace")
        low = pat.lower()
        if low.startswith("0x"):
            low = low[2:]
        low = low.replace(" ", "")
        if low and all(c in "0123456789abcdef" for c in low) and len(low) % 2 == 0:
            try:
                return bytes.fromhex(low)
            except ValueError:
                return b""
        return pat.encode("latin-1", "replace")     # bare text -> ascii

    def _extract(self, regions):
        default = os.path.join(os.path.dirname(os.path.abspath(self._blob_src)),
                               os.path.splitext(os.path.basename(self._blob_src))[0]
                               + "_regions")
        self.push_screen(
            PromptScreen(f"extract {len(regions)} region(s) to (enter to confirm):",
                            default),
            lambda d: self._do_extract(regions, d))

    def _do_extract(self, regions, outdir):
        if not outdir:
            return
        try:
            os.makedirs(outdir, exist_ok=True)
            n = 0
            for r in regions:
                ext = _CARVE_EXT.get(r.get("format")) or "raw"
                name = f"{n:04d}_0x{r['offset']:08x}_{r['kind']}.{ext}"
                with open(os.path.join(outdir, name), "wb") as f:
                    f.write(self._region_bytes(r))
                n += 1
            self.query_one("#title", Static).update(
                Text(f" extracted {n} region(s) -> {outdir}", style=f"bold {ACCENT}"))
        except OSError as e:
            self.query_one("#title", Static).update(
                Text(f" extract failed: {e}", style=f"bold {SEV['alert']}"))

    def _display_name(self):
        """The name shown in the title/tree: a breadcrumb when inside a region,
        the plain basename otherwise."""
        if self._region_view is not None:
            label, r = self._region_view
            fmt = r.get("transform") or r.get("format") or r["kind"]
            where = f"region {label}" if isinstance(label, int) else str(label)
            return (f"{os.path.basename(self._blob_src)} > "
                    f"{where} ({fmt} @ 0x{r['offset']:08x})")
        return os.path.basename(self.src)

    def _make_work(self):
        self._discard_work()
        if os.path.getsize(self.src) > _LARGE_FILE:
            # too big to copy on open; browse it in place, read-only (a disk image
            # is not something you edit field-by-field anyway). Descended regions
            # are small and still get an editable working copy.
            self.work = self.src
            self._readonly = True
            self._work_is_temp = False
        else:
            ext = os.path.splitext(self.src)[1]
            fd, self.work = tempfile.mkstemp(suffix=ext or ".bin", prefix="acidcat_tui_")
            os.close(fd)
            shutil.copyfile(self.src, self.work)
            self._readonly = False
            self._work_is_temp = True
        self.dirty = False
        self._backed_up = False
        self._undo = []
        self._src_stat = self._stat_src()
        self._force_stale = False

    def _stat_src(self):
        """(mtime_ns, size) of the source file, or None if it can't be stat'd.
        Taken at open and after each save; save refuses on a mismatch so an
        external change is never silently clobbered (and the _original backup
        never captures bytes the user wasn't editing)."""
        try:
            st = os.stat(self.src)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _discard_work(self):
        w = self.work
        self.work = None
        # only delete a temp we created; never the original (read-only large file)
        if w and self._work_is_temp and os.path.isfile(w):
            try:
                os.unlink(w)
            except OSError:
                pass
        self._work_is_temp = False

    @staticmethod
    def _minimal_delta(old, new):
        """The minimal changed region between two byte strings as
        (start, old_segment, new_segment) -- common prefix and suffix trimmed.
        A same-length field/hex patch yields a few-byte delta even on a huge
        file, so undo history holds byte ranges, not whole-file snapshots."""
        n = min(len(old), len(new))
        start = 0
        while start < n and old[start] == new[start]:
            start += 1
        # suffix length, not crossing into the prefix on either side
        suf = 0
        while (suf < n - start
               and old[len(old) - 1 - suf] == new[len(new) - 1 - suf]):
            suf += 1
        return start, old[start:len(old) - suf], new[start:len(new) - suf]

    def _apply_to_work(self, new_bytes):
        """Write edited bytes to the working copy (no disk write to the original
        yet), recording a minimal-diff undo delta, and refresh."""
        with open(self.work, "rb") as f:
            old = f.read()
        start, old_seg, new_seg = self._minimal_delta(old, new_bytes)
        if old_seg == new_seg:                # nothing actually changed
            return
        self._undo.append((start, old_seg, new_seg))
        self._redo = []           # a fresh edit invalidates the redo history
        self._undo = self._undo[-_UNDO_CAP:]
        # cap by total delta bytes so history cannot pin gigabytes; the most
        # recent delta always survives.
        while (len(self._undo) > 1
               and sum(len(o) + len(n) for _s, o, n in self._undo) > _UNDO_BYTES_CAP):
            self._undo.pop(0)
        with open(self.work, "wb") as f:
            f.write(new_bytes)
        self.dirty = True         # cheap: no whole-file compare on the hot path
        self._load()

    def _recompute_dirty(self):
        """Dirty iff the working copy differs from the saved file. Only called on
        undo/redo (rare), so the whole-file compare stays off the edit hot path;
        a plain edit sets dirty=True directly."""
        try:
            with open(self.work, "rb") as f:
                w = f.read()
            with open(self.src, "rb") as f:
                self.dirty = w != f.read()
        except OSError:
            self.dirty = True

    def _apply_delta(self, start, seg_out, seg_in):
        """Replace the bytes at `start` currently equal to `seg_out` with
        `seg_in` in the working copy (the shared undo/redo primitive)."""
        with open(self.work, "rb") as f:
            data = f.read()
        with open(self.work, "wb") as f:
            f.write(data[:start] + seg_in + data[start + len(seg_out):])

    def action_undo(self):
        if not self._undo:
            self.notify("nothing to undo")
            return
        start, old_seg, new_seg = self._undo.pop()
        self._apply_delta(start, new_seg, old_seg)      # revert new -> old
        self._redo.append((start, old_seg, new_seg))
        self._redo = self._redo[-_UNDO_CAP:]
        self._recompute_dirty()
        self._load()
        self.notify("undid last edit")

    def action_redo(self):
        if not self._redo:
            self.notify("nothing to redo")
            return
        start, old_seg, new_seg = self._redo.pop()
        self._apply_delta(start, old_seg, new_seg)      # re-apply old -> new
        self._undo.append((start, old_seg, new_seg))
        self._undo = self._undo[-_UNDO_CAP:]
        self._recompute_dirty()
        self._load()
        self.notify("redid last edit")

    def action_save(self):
        if not self.work:
            return
        if self._readonly:
            self.notify("read-only: this file is too large for in-place editing; "
                        "descend into a region to edit it")
            return
        if not self.dirty:
            self.notify("no unsaved changes")
            return
        if self._src_stat is not None and self._stat_src() != self._src_stat:
            if not self._force_stale:
                self._force_stale = True
                # this is action_save, bound to ctrl+s; plain s is strip, so
                # naming the wrong key would silently strip instead of forcing
                self.notify("file changed on disk since it was opened; "
                            "press ctrl+s again to overwrite it anyway",
                            severity="error")
                return
        self._force_stale = False
        try:
            with open(self.work, "rb") as f:
                data = f.read()
            # back up the pristine original only on the first save; later saves
            # overwrite without clobbering that backup.
            _written, backup = writer.commit(self.src, data,
                                             overwrite=self._backed_up)
        except (OSError, ValueError) as e:
            self.notify(f"save failed: {e}", severity="error")
            return
        if backup:
            self._backed_up = True
        self.dirty = False
        self._src_stat = self._stat_src()
        self._load()
        if backup:
            msg = f"saved; backup {os.path.basename(backup)}"
        elif not self._backed_up and os.path.exists(writer.backup_path(self.src)):
            # first save found a <name>_original already on disk and kept it;
            # that file may predate acidcat and not hold this original
            msg = "saved; existing backup kept"
        else:
            msg = "saved"
        self.notify(msg)

    def action_request_quit(self):
        if self.dirty:
            self.push_screen(
                ConfirmScreen("unsaved changes -- save before quitting?"),
                self._resolve_pending(lambda: self.exit()))
        else:
            self.exit()

    def _resolve_pending(self, proceed):
        """Return a ConfirmScreen callback: save/discard run `proceed`, cancel
        stays. Used for both quit and open-another-file with unsaved edits."""
        def cb(choice):
            if choice == "save":
                self.action_save()
                if self.dirty:      # save failed: keep the session and the edits
                    return
                proceed()
            elif choice == "discard":
                self.dirty = False
                proceed()
        return cb

    def _load(self):
        """Walk the working copy and (re)build the tree + panes. Also the refresh
        after an edit or after opening a new file."""
        self.fsize = os.path.getsize(self.work)
        try:
            self.fmt, self.chunks, self.warns = walk_file(self.work, deep=True)
        except Unsupported as e:
            self.fmt, self.chunks, self.warns = "unsupported", [], [str(e)]
        except Exception as e:
            # a crafted/corrupt file may make a walker raise something other
            # than Unsupported; the TUI opens files on mount, so this must not
            # crash the session (the DoS threat model is degrade-not-die)
            self.fmt, self.chunks, self.warns = (
                "walk failed", [], [f"{e.__class__.__name__}: {e}"])
        self._prefer_be = self.fmt in _BE_FMTS
        # Three states, not two. An empty finding list meant BOTH "scanned it,
        # nothing there" and "never scanned it", and the panel rendered both as
        # "clean: no findings" -- a check that did not run reading as a pass,
        # one panel over from the test file written to prevent exactly that.
        self.scan_note = None
        if self._readonly:
            self.findings = []
            self.scan_note = ("not scanned: the file is too large to scan "
                              "whole, so nothing here is a verdict")
        else:
            try:
                self.findings = ac_anom.scan(self.work, self.fmt, self.chunks, self.warns)
            except Exception as e:
                self.findings = []
                self.scan_note = (f"scan failed ({e.__class__.__name__}); this "
                                  f"file was NOT screened")

        head = Text()
        head.append(f" {self._display_name()} ", style=f"bold {ACCENT}")
        head.append(f" {self.fmt}  {self.fsize:,} bytes  "
                    f"{len(self.chunks)} chunks", style=SOFT)
        if self._region_view is not None:
            head.append("   [u back to regions]", style=DIM)
        if self.dirty:
            head.append("   ● UNSAVED", style=f"bold {SEV['alert']}")
        self.query_one("#title", Static).update(head)

        prof = edit_profile(self.work)
        self._profile = prof[0] if prof else None
        tree = self.query_one("#tree", Tree)
        # a rebuild (after an edit / undo / save) must not dump the user at the
        # root with everything collapsed: remember the highlighted node and the
        # expanded chunks by stable key (chunk index / field ordinal), restore
        # them after the rebuild.
        cur_key = (self._nodekey.get(id(self._cur_node))
                   if self._cur_node is not None else None)
        expanded = {self._nodekey[id(n)] for n in tree.root.children
                    if n.is_expanded and id(n) in self._nodekey}
        tree.clear()
        self._nodemeta = {}
        self._morerows = {}       # id(node) -> chunk index, for the "more rows" node
        self._morechunks = set()  # id(node) for the "more chunks" node
        self._editval = {}
        self._textfield = {}
        self._nodekey = {}
        self._allnodes = []       # rebuilt each load, for goto/search/finding jumps
        self._xref = {}
        keyed = {}
        tree.root.set_label(Text(self._display_name(), style=f"bold {FG}"))
        tree.root.data = (0, self.fsize, ACCENT)
        self._nodemeta[id(tree.root)] = (0, self.fsize, ACCENT)
        from acidcat.core.infra.sniff import AUDIO_SAMPLE_IDS
        cbudget = getattr(self, "_chunkbudget", _CHUNK_CAP)
        for i, c in enumerate(self.chunks[:cbudget]):
            accent = PALETTE[i % len(PALETTE)]
            cid = str(c.get("id", "?")).strip()
            # Mark the chunk that actually holds sample data. Every field node in
            # this tree is a selectable, playable region and almost none of them
            # are audio, so without a mark the only way to find out which is
            # which was to press play and get a burst of noise.
            is_audio = cid in AUDIO_SAMPLE_IDS
            lbl = Text()
            lbl.append("~ " if is_audio else "  ", style=f"bold {TEAL}")
            lbl.append(f"{cid:<6}",
                       style=f"bold {TEAL}" if is_audio else f"bold {accent}")
            lbl.append(f"0x{c.get('offset', 0):08x}  ", style=DIM)
            lbl.append(f"{c.get('size', 0):,}b  ", style=SOFT)
            lbl.append(trim_size_echo(c.get("summary", ""), c.get("size", 0)),
                       style=FG)
            if is_audio:
                lbl.append("  [playable]", style=TEAL)
            node = tree.root.add(lbl)
            node.data = (c.get("offset", 0), c.get("size", 0), accent)
            self._nodemeta[id(node)] = node.data
            self._nodekey[id(node)] = ("chunk", i)
            keyed[("chunk", i)] = node
            self._allnodes.append((node, c.get("offset", 0), c.get("size", 0)))
            for j, fl in enumerate(c.get("fields", [])):
                abs_off = _field_abs(c, fl)
                flbl = Text()
                flbl.append(f"{fl['name']}", style=SOFT)
                flbl.append(" = ", style=DIM)
                flbl.append(f"{fl['value']!s}", style=accent)
                if fl.get("note"):
                    flbl.append(f"  {fl['note']}", style=DIM)
                fnode = node.add_leaf(flbl)
                fnode.data = (abs_off, fl.get("len") or 0, accent)
                self._nodemeta[id(fnode)] = fnode.data
                self._nodekey[id(fnode)] = ("field", i, j)
                keyed[("field", i, j)] = fnode
                if fl.get("xref") is not None:
                    self._xref[id(fnode)] = fl["xref"]
                if abs_off is not None:
                    self._allnodes.append((fnode, abs_off, fl.get("len") or 0))
                if abs_off is not None:
                    self._editval[id(fnode)] = (fl.get("value"), fl.get("enc"),
                                                fl.get("raw"))
                    mf = text_field_for(self._profile, fl["name"])
                    if mf is not None:
                        self._textfield[id(fnode)] = mf
            # per-element rows: MIDI events, MP3 frames, device params, etc. --
            # the deep detail inspect --frames/--verbose shows. Rows carry no
            # uniform byte offset, so a row node uses its own if present else the
            # chunk's range for the hex pane.
            rows = c.get("rows") or []
            # The cap keeps a 100k-event MIDI file from building 100k widgets,
            # but a counted-and-unreachable row is still a part of the file you
            # cannot walk to. `+` raises this chunk's budget and reloads.
            budget = self._rowbudget.get(i, _ROW_CAP)
            for k, row in enumerate(rows[:budget]):
                rlbl = Text("  ".join(f"{k2}={v}" for k2, v in row.items()),
                            style=SOFT)
                roff = row.get("offset") if isinstance(row.get("offset"), int) else None
                rlen = row.get("size") if isinstance(row.get("size"), int) else 0
                rnode = node.add_leaf(rlbl)
                rnode.data = ((roff, rlen, accent) if roff is not None
                              else node.data)
                self._nodemeta[id(rnode)] = rnode.data
                self._nodekey[id(rnode)] = ("row", i, k)
                keyed[("row", i, k)] = rnode
            if len(rows) > budget:
                more = node.add_leaf(
                    Text(f"... {len(rows) - budget:,} more rows  (+ to show more)",
                         style=DIM))
                self._nodemeta[id(more)] = node.data
                self._morerows[id(more)] = i
        if len(self.chunks) > cbudget:
            # counted, named, and reachable -- the same treatment rows get. A
            # silently shortened tree would make a truncated file look complete.
            more = tree.root.add_leaf(
                Text(f"... {len(self.chunks) - cbudget:,} more chunks  "
                     f"(+ to show more)", style=DIM))
            self._nodemeta[id(more)] = tree.root.data
            self._morechunks.add(id(more))
        tree.root.expand()
        for ek in expanded:
            n = keyed.get(ek)
            if n is not None:
                n.expand()
        self._render_anomalies()
        target = keyed.get(cur_key)
        if target is not None:
            if target.parent is not None and not target.parent.is_expanded:
                target.parent.expand()
            self._cur_node = target
            # node lines are computed on the next refresh; moving now lands on -1
            self.call_after_refresh(tree.move_cursor, target)
            off, length, accent = self._nodemeta[id(target)]
            self._show(off, length, accent, self._node_name(target),
                       self._edit_hint(target, off, length))
        else:
            self._show(0, self.fsize, ACCENT, os.path.basename(self.src), "")

    def _render_anomalies(self):
        panel = self.query_one("#anom", Static)
        # the box it shares with the filename goes orange only when there is
        # something to see -- a permanently alarmed border says nothing
        self.query_one("#idbox").set_class(bool(self.findings), "findings")
        t = Text()
        t.append("forensics  ", style=f"bold {ACCENT}")
        if not self.findings:
            note = getattr(self, "scan_note", None)
            # amber is the tool being honest about its limits, kept distinct
            # from orange, which means a finding or an unsaved edit
            t.append(note or "clean: no findings", style=AMBER if note else SOFT)
            panel.update(t)
            return
        # severity legend so the colors are readable, then every finding
        # numbered (press f to jump the tree/hex to the next one).
        t.append(f"{len(self.findings)} finding(s)   ", style=SOFT)
        t.append("alert", style=f"bold {SEV['alert']}")
        t.append(" / ", style=DIM)
        t.append("warn", style=f"bold {SEV['warn']}")
        t.append(" / ", style=DIM)
        t.append("notice", style=f"bold {SEV['notice']}")
        t.append("   (f = jump)\n", style=DIM)
        for i, f in enumerate(self.findings):
            sev = f.get("severity", "notice")
            marker = ">" if i == self._finding_idx else " "
            t.append(f" {marker}{i + 1:>2} ", style=f"bold {ACCENT}" if
                     i == self._finding_idx else DIM)
            t.append(f"{sev:<7}", style=f"bold {SEV.get(sev, SOFT)}")
            t.append(f"0x{f.get('offset', 0):08x} ", style=DIM)
            t.append(f"{f.get('message', '')}\n", style=FG)
        panel.update(t)
        self._scroll_finding_into_view()

    def _scroll_finding_into_view(self):
        """Keep the finding `f` just selected on screen.

        The box is six rows and holds one line per finding, so past the fourth
        one the `>` marker moved somewhere nobody could see: pressing f again
        and again showed an unchanging panel while the tree and hex pane jumped
        around, which reads as f being broken rather than as the list being
        longer than the box.

        Best-effort. It is a cosmetic scroll, and the jump itself, the marker,
        and the notification have all already happened.
        """
        if self._finding_idx < 0:
            return
        try:
            box = self.query_one("#idbox")
            # inside #idbox: #title, then #anom, whose first line is the legend
            head = self.query_one("#title").size.height or 1
            box.scroll_to(y=max(0, head + 1 + self._finding_idx - 1),
                          animate=False)
        except Exception:
            pass

    @staticmethod
    def _node_name(node):
        lbl = node.label
        return (lbl.plain if isinstance(lbl, Text) else str(lbl)).strip()

    def _show(self, off, length, accent, name, note, spans=None):
        detail = self.query_one("#detail", Static)
        d = Text()
        d.append(name, style=f"bold {accent}")
        if off is None:
            d.append("   (derived, no byte range)", style=DIM)
        elif f"0x{off:08x}" not in name:
            d.append(f"   @ 0x{off:08x}   {length:,} bytes", style=SOFT)
        # else: the tree label already carries this offset. Repeating it put
        # the same two facts on the line twice and pushed it to 110 columns
        # against a 66-column pane, which is most of the wrapping in the
        # multi-pane view -- the pane was not too small, the line was too long.
        if note:
            d.append(f"\n{note}", style=SOFT)
        # A status line, so it clips rather than reflows. Wrapped, one long
        # summary silently stole a row from the pane below and the layout
        # jumped as you moved through the tree -- the detail is a glance, and
        # the full text is a keypress away in the pane that has room for it.
        d.no_wrap = True
        d.overflow = "ellipsis"
        detail.update(d)
        self._cur_region = (off, length, accent)
        self._cur_spans = spans
        if self._view == "hex":
            self.query_one("#hex", Static).update(
                hex_text(self.work, off, length, accent, spans,
                         self._hex_width()))
        # in a viz mode the pane shows a whole-file view; a node highlight leaves it

    # pane id -> the class that gives it the screen. Not every pane has one:
    # #idbox is six rows by design, so filling the screen with it would be a
    # worse view of the same list, not a better one.
    _ZOOM_FOR = {"tree": "zoom-tree", "hexwrap": "zoom-hex"}
    # tab order, in the order they appear on screen: top-left, bottom-left,
    # right. #idbox is here because it scrolls -- see _move_pane.
    _PANES = ("idbox", "tree", "hexwrap")

    def _focused_pane(self):
        """Which pane owns focus, walking up from whatever widget has it.

        The hex pane's focusable child is #hex, not the #hexwrap scroller, so a
        direct id check misses it.
        """
        node = self.focused
        while node is not None:
            if getattr(node, "id", None) in self._PANES:
                return node.id
            node = node.parent
        return "hexwrap" if self._view == "hex" else "tree"

    def action_focus_pane_back(self):
        self._move_pane(-1)

    def action_focus_pane(self):
        """Move focus to the next pane (tab).

        Without this the hex pane is unreachable: focus starts on the tree and
        never leaves, so arrow keys always drive the tree and the hex view
        cannot be scrolled at all -- and it is a VerticalScroll holding up to
        _HEX_CAP bytes, which is far more than one screen.
        """
        self._move_pane(1)

    def _pane_visible(self, pane):
        """Is this pane actually on screen right now?

        A zoom hides the other pane with `display: none` on its container, and
        the widget itself still reports display=True -- so asking the widget is
        not enough.
        """
        node = self.query_one(f"#{pane}")
        while node is not None:
            if getattr(node, "display", True) is False:
                return False
            node = node.parent
        return True

    def _move_pane(self, step):
        """Cycle focus through the panes you can see.

        Skipping the hidden ones is the whole job. Zoomed into the hex view,
        tab used to hand focus to the tree, which is hidden -- so the arrow
        keys drove a cursor nobody could see and the hex pane jumped to a field
        you had not chosen. Navigating blind reads as "I cannot change fields".

        #idbox joined this cycle for the same reason the hex pane did. It is a
        fixed six rows holding a legend plus one line per finding, so a file
        with five findings puts the rest below the fold, and it was reachable
        only with a mouse. A forensics tool whose forensics panel needs a mouse
        to read is not one you can drive over ssh.
        """
        order = [p for p in self._PANES if self._pane_visible(p)]
        if len(order) < 2:
            self.notify("nothing else on screen -- z to leave zoom",
                        severity="warning")
            return
        cur = self._focused_pane()
        i = order.index(cur) if cur in order else 0
        nxt = order[(i + step) % len(order)]
        self.query_one(f"#{nxt}").focus()
        self.notify(f"focus: {nxt}")

    def action_zoom(self):
        """Give the focused pane the whole screen, or hand it back (z).

        Zooms what you are looking at rather than walking a fixed order -- and
        focuses it, so the arrow keys drive the thing that just filled the
        screen. A hex row is 76 columns against a pane that is 52% of the
        terminal, so below about 154 columns the dump folds without this.
        """
        target = self._focused_pane()
        want = self._ZOOM_FOR.get(target)
        if want is None:
            # a pane with no zoom class (#idbox). Say so rather than raising or
            # silently zooming something the user was not looking at.
            self.notify(f"{target} does not zoom -- tab to the tree or hex pane",
                        severity="warning")
            return
        for cls in set(self._ZOOM_FOR.values()):
            self.screen.remove_class(cls)
        if self._zoom == want:
            self._zoom = None
            self.query_one("#tree").focus()
            self.call_after_refresh(self._paint_bytes)
            self.notify("zoom: off")
            return
        self.screen.add_class(want)
        self._zoom = want
        self.query_one(f"#{target}").focus()
        # after the refresh: the class is set but the layout has not run
        # yet, so measuring the pane now returns its old size
        self.call_after_refresh(self._paint_bytes)
        self.notify(f"zoom: {target}")

    def action_cycle_view(self):
        """Cycle the hex pane: hex -> entropy -> hilbert -> histogram."""
        order = ["hex", "entropy", "hilbert", "histogram"]
        if self._hexedit:
            self._exit_hexedit()
        if self._edit_target:
            self.action_cancel_edit()
        self._view = order[(order.index(self._view) + 1) % len(order)]
        self._paint_bytes()
        self.notify(f"byte view: {self._view}")

    # Vertical axes offered per view. The hex dump and the Hilbert map have no
    # magnitude axis to rescale, so they are absent rather than given a mode
    # that would do nothing -- S says so instead of appearing to work.
    _VIZ_SCALES = {
        "entropy": ("absolute", "auto"),
        "histogram": ("absolute", "log", "clip"),
    }

    def _scale_for(self, mode):
        opts = self._VIZ_SCALES.get(mode)
        if not opts:
            return None
        return opts[self._viz_scale.get(mode, 0) % len(opts)]

    def action_viz_scale(self):
        """Cycle the vertical axis of the current byte view (S).

        Reported: the entropy chart is pinned to the ceiling on most real
        files, because audio data sits at 7.9 of 8 and the axis is the full
        theoretical range. Everything interesting happens in the last two
        percent of the plot, where it cannot be seen. `auto` rescales to the
        data actually present; the caption always names the axis in use, since
        a rescaled chart and an absolute one look identical.
        """
        opts = self._VIZ_SCALES.get(self._view)
        if not opts:
            self.notify(f"{self._view} has no scale to change "
                        f"(b cycles to entropy or histogram)",
                        severity="warning")
            return
        i = (self._viz_scale.get(self._view, 0) + 1) % len(opts)
        self._viz_scale[self._view] = i
        self._paint_bytes()
        self.notify(f"{self._view} scale: {opts[i]}")

    def action_viz_scope(self):
        """Toggle the byte views between the whole file and the selected node (r).

        The views answered one question -- what does this FILE look like -- and
        the tree next to them is a list of regions you might want that same
        picture of. A 40-byte fmt chunk inside a 60 MB WAV occupies a single
        column of a whole-file entropy plot.
        """
        if self._view == "hex":
            self.notify("the hex view already follows the selected node "
                        "(b cycles to a graph)", severity="warning")
            return
        self._viz_scope = "region" if self._viz_scope == "file" else "file"
        self._paint_bytes()
        lo, hi, label = self._viz_range()
        self.notify(f"viz scope: {label} ({hi - lo:,} bytes)")

    def _viz_range(self):
        """(start, end, label) the graph views should cover.

        Falls back to the whole file when scope is `region` but the selected
        node has no byte range of its own -- a derived field, or nothing
        selected yet. The label carries that fallback rather than hiding it,
        because a picture captioned "region" that is really the whole file is
        worse than no scoping at all.
        """
        if self._viz_scope == "region":
            node = self._cur_node
            meta = self._nodemeta.get(id(node)) if node else None
            if meta and meta[0] is not None and meta[1]:
                off, length, _ = meta
                lo = max(0, min(int(off), self.fsize))
                hi = max(lo, min(lo + int(length), self.fsize))
                if hi > lo:
                    return lo, hi, f"{self._short_name(node)} @ 0x{lo:08x}"
            return 0, self.fsize, "whole file (selection has no bytes)"
        return 0, self.fsize, "whole file"

    def _short_name(self, node):
        """The head of a tree row, for a caption.

        A row is "~ data  0x00000024  240,000b  audio payload, 1.361 s
        [playable]" -- correct in the tree and far too long above a chart that
        already prints its own offset and size.
        """
        name = (self._node_name(node) or "region").lstrip("~ ").strip()
        return re.split(r"\s{2,}", name)[0][:24] or "region"

    def _paint_bytes(self):
        """Draw the byte pane at the size it has right now.

        Every view in this pane is rendered to a fixed character grid sized
        from the pane, so any layout change leaves the drawing stale until
        something repaints it. Zooming did exactly that: the pane doubled and
        the visualization kept its old dimensions until you cycled the view
        away and back. A terminal resize had the same problem.
        """
        pane = self.query_one("#hex", Static)
        if self._view == "hex":
            off, length, accent = self._cur_region
            pane.update(hex_text(self.work, off, length, accent,
                                 self._cur_spans, self._hex_width()))
        else:
            pane.update(self._viz_render(self._view))

    def on_resize(self, event=None):
        """Repaint on a terminal resize, for the same reason zoom does."""
        if self.work:
            self.call_after_refresh(self._paint_bytes)

    # of #hexwrap's outer width, the row never gets: its own border (2),
    # #hex's padding (2), and the vertical scrollbar (2).
    _HEX_CHROME = 6

    def _hex_width(self):
        """Bytes per row for the current pane.

        16 needs 76 columns and half a terminal only reaches that at 156, so a
        constant meant the grid folded on any ordinary window -- and a folded
        hex grid loses the property that makes it a grid.

        The scrollbar is subtracted whether or not one is showing, and that is
        the point. Whether it shows depends on how tall the content is, which
        depends on the width chosen here -- so measuring the live scrollbar
        feeds the answer back into itself and lands one layout behind. That is
        what made the wrapping intermittent: a chunk long enough to scroll lost
        two columns after the width had already been picked, and stepping to a
        short field and back appeared to "fix" it because the stale measurement
        had flipped. Reserving the gutter always costs at most one width step
        at a borderline size, and never wraps.
        """
        try:
            return row_width_for(self.query_one("#hexwrap").size.width
                                 - self._HEX_CHROME)
        except Exception:
            return 16

    def _viz_width(self):
        try:
            return max(24, self.query_one("#hexwrap").size.width - 4)
        except Exception:
            return 72

    # rows the pane spends on a heading, the caption under it, blank lines and
    # the legend -- everything that is not the drawing itself
    _VIZ_CHROME_ROWS = 7

    def _viz_rows(self):
        """Rows available to a visualization, after its own headings."""
        try:
            h = self.query_one("#hexwrap").size.height - 2   # border
        except Exception:
            return 12
        return max(4, h - self._VIZ_CHROME_ROWS)

    def _viz_chart_height(self):
        """Rows for a column chart. Capped: past a point more rows stop adding
        readable resolution and the eye loses the whole shape at a glance."""
        return max(4, min(24, self._viz_rows()))

    def _hilbert_order(self):
        """Largest Hilbert order whose map fits the pane.

        The map is 2**order square, drawn with half-blocks, so it costs `side`
        columns and `side / 2` rows. Order is what sets how many bytes fold
        into one cell, so fitting a bigger one to a zoomed pane is not
        cosmetic -- it is more of the file actually resolved.
        """
        cols, rows = self._viz_width(), self._viz_rows()
        best = 4
        for order in (5, 6, 7, 8):
            side = 1 << order
            if side <= cols and side // 2 <= rows:
                best = order
        return best

    def _viz_render(self, mode):
        if not self.fsize:
            t = Text()
            t.append("  (no bytes to visualize)", style=DIM)
            return t
        # Entropy and hilbert stream from the file and cover all of it.
        # Only the histogram still needs bytes in hand, and it now says how
        # many it read -- this used to read 8 MB and caption every view
        # "(whole file)", which was false for anything larger.
        if mode == "entropy":
            return self._viz_entropy()
        if mode == "hilbert":
            return self._viz_hilbert()
        lo, hi, scope = self._viz_range()
        want = hi - lo
        return self._viz_histogram(_read(self.work, lo, min(want, _VIZ_READ)),
                                   scope=scope, capped=want > _VIZ_READ)

    def _viz_caption(self, t, scope, sampled, scale_label, transformed=False):
        """The line under every graph: what it covers, how exact it is, and
        which axis it used. All three change what the picture means, so none of
        them is optional decoration.

        `transformed` is passed in rather than sniffed out of `scale_label`.
        Deciding a colour by reading the wording of a string is the defect this
        codebase has now fixed twice (the anomaly checks dispatching on a
        display label, the walker warnings), and it starts exactly like this.
        """
        t.append(scope, style=SOFT)
        t.append("  (sampled)" if sampled else "", style=PEND)
        if scale_label:
            t.append(f"  scale {scale_label}", style=PEND if transformed else SOFT)
        t.append("\n")

    def _viz_entropy(self):
        lo, hi, scope = self._viz_range()
        ent, size, sampled = viz.file_entropy(
            self.work, self._viz_width(), start=lo, end=hi)
        if not ent:
            return Text("  (no bytes to visualize)", style=DIM)
        mode = self._scale_for("entropy")
        norm, _vlo, _vhi, label = viz.scale_values(
            ent, mode, floor=0.0, ceiling=8.0)
        t = Text()
        t.append("entropy  ", style=f"bold {ACCENT}")
        t.append(f"min {min(ent):.2f}  mean {sum(ent) / len(ent):.2f}  "
                 f"max {max(ent):.2f} bits/byte  ", style=SOFT)
        self._viz_caption(t, scope, sampled, label,
                          transformed=mode != "absolute")
        t.append("0-8 bits/byte; flat 8 = compressed   "
                 "(r = region, S = scale)\n\n", style=DIM)
        # A column chart rather than a one-row sparkline. Eight bits squeezed
        # into a single cell gives eight distinguishable levels; over the rows
        # the pane actually has it is eight per row, and the difference
        # between 7.6 and 7.9 bits -- compressed versus encrypted -- becomes
        # something you can see instead of something you have to measure.
        #
        # Colour tracks the DRAWN height, not the raw value, so it rescales
        # with the axis. Under `auto` a bar at the top of a 7.90-7.95 window is
        # the hottest thing on screen, which is the correct reading of a chart
        # whose caption says that is the window.
        rows = self._viz_chart_height()
        blocks = " ▁▂▃▄▅▆▇█"
        levels = [n * rows for n in norm]
        for r in range(rows - 1, -1, -1):
            for col, filled in enumerate(levels):
                frac = max(0.0, min(1.0, filled - r))
                t.append(blocks[int(frac * (len(blocks) - 1))],
                         style=ramp_color(norm[col]))
            t.append("\n")
        if self._viz_scope != "region":
            self._viz_mark_container_end(t, len(ent), size)
        return t

    def _viz_mark_container_end(self, t, columns, size):
        """Draw where the container says it stops.

        A hot band to the RIGHT of this mark is appended data -- the
        polyglot and trailing-payload tell, and most of the reason to look
        at an entropy view at all. Without it the eye has no reference for
        where the file should have ended.
        """
        end = self._declared_end()
        if not end or not size or end >= size:
            return
        col = max(0, min(columns - 1, int(end * columns / size)))
        t.append(" " * col, style=DIM)
        t.append("^\n", style=f"bold {PEND}")
        # on its own line, left-aligned. Hung off the caret, the label started
        # at the caret's column, so a container ending late in the file pushed
        # its own explanation off the right edge of the pane.
        t.append(f"container ends at 0x{end:08x}; "
                 f"{size - end:,} bytes follow\n", style=PEND)

    def _declared_end(self):
        """Where the outermost walked container claims to stop, or None."""
        best = None
        for c in self.chunks:
            end = (c.get("offset") or 0) + (c.get("size") or 0)
            if best is None or end > best:
                best = end
        return best

    def _viz_hilbert(self):
        lo, hi, scope = self._viz_range()
        grid, side, sampled = viz.hilbert_from_file(
            self.work, order=self._hilbert_order(), start=lo, end=hi)
        t = Text()
        t.append("hilbert  ", style=f"bold {ACCENT}")
        t.append(f"{side}x{side}; adjacent cells are adjacent bytes  ", style=SOFT)
        self._viz_caption(t, scope, sampled, None)
        t.append("\n")
        for y in range(0, side, 2):
            for x in range(side):
                top = grid[y][x]
                bot = grid[y + 1][x] if y + 1 < side else None
                t.append("▀", style=f"{byte_color(top)} on {byte_color(bot)}")
            t.append("\n")
        t.append("\n")
        for b, label in ((0x41, " ascii  "), (0x80, " high  "), (0x00, " null/ctrl")):
            t.append("▀", style=byte_color(b))
            t.append(label, style=SOFT)
        t.append("\n")
        return t

    def _viz_histogram(self, data, scope="whole file", capped=False):
        from acidcat.core.primitives.signal import byte_counts

        w = self._viz_width()
        counts = byte_counts(data)
        mode = self._scale_for("histogram")
        norm, _lo, _hi, label = viz.scale_values(counts, mode, floor=0.0)
        t = Text()
        t.append("byte histogram  ", style=f"bold {ACCENT}")
        t.append(f"0x00 .. 0xff over {len(data):,} bytes  ", style=SOFT)
        self._viz_caption(t, scope + (", read capped" if capped else ""),
                          False, label, transformed=mode != "absolute")
        t.append(f"peak {max(counts):,} at 0x{counts.index(max(counts)):02x}"
                 if any(counts) else "no bytes", style=DIM)
        t.append("   (r = region, S = scale)\n\n", style=DIM)
        # Drawn from the SCALED values, and coloured from the same array by the
        # per-cell peak, so the bar you see and the colour it is drawn in came
        # out of one dataset. Under `log` or `clip` a file that is 90% zero
        # padding stops flattening its own histogram into a single spike and a
        # flat line, which was every padded sampler bank in the corpus.
        rows = viz.braille_line(norm, width=w, height=self._viz_chart_height(),
                                vmin=0.0, vmax=1.0, fill=True)
        peaks = viz.column_peaks(norm, w)
        for row in rows:
            for x, ch in enumerate(row):
                t.append(ch, style=ramp_color(peaks[x] if x < len(peaks) else 0.0))
            t.append("\n")
        return t

    def _audio_span(self):
        """(start, end) of the file's sample data, or None.

        Structural, not statistical: inside a walked container the chunk id IS
        the answer, and no heuristic beats it.
        """
        from acidcat.core.infra.sniff import AUDIO_SAMPLE_IDS
        for c in self.chunks:
            if str(c.get("id", "")).strip() in AUDIO_SAMPLE_IDS:
                base = c.get("payload_base", (c.get("offset") or 0) + 8)
                return base, base + (c.get("size") or 0)
        return None

    def _region_is_audio(self, off, length):
        """True only when playing this region would actually produce sound.

        Playback starts AT `off` and reinterprets what it finds as PCM, so the
        question is what sits at the START of the region -- not whether most of
        it overlaps the audio somewhere. A parent node that spans a header plus
        the samples overlaps by well over half and still opens with noise.

        Returns None when no walker located raw PCM in this file at all. That
        is not the same as "safe": in a compressed format there is no region
        that plays as PCM, so the caller must warn on None rather than treat an
        unknown as a yes.
        """
        span = self._audio_span()
        if span is None:
            return None
        lo, hi = span
        # tolerate landing on the chunk header a few bytes before the payload,
        # which is what selecting the parent node does
        if not (lo - 8 <= off < hi):
            return False
        overlap = max(0, min(off + length, hi) - max(off, lo))
        return overlap >= 0.5 * max(1, length)

    def action_play(self):
        """Audition the selected region's bytes as raw PCM (p); '.' stops."""
        if not play.have_audio():
            self.notify("no audio player found (install ffmpeg for ffplay)",
                        severity="warning")
            return
        off, length, _ = self._cur_region
        if off is None or not length:
            self.notify("highlight a region with bytes to play", severity="warning")
            return

        # Auditioning a header, a tag or a chunk of text as PCM produces a burst
        # of loud noise at whatever volume the user happens to be on. That is a
        # real hazard with headphones, and it is easy to hit -- every field node
        # in the tree is a selectable region, and almost none of them are audio.
        is_audio = self._region_is_audio(off, length)
        if is_audio is not True:
            if is_audio is False:
                msg = (f"{self._chunk_name_at(off)} is not the audio payload. "
                       f"Playing it as PCM will be loud noise, not sound. Continue?")
            else:
                # No walker located raw PCM here, which is every compressed
                # format. Nothing in the file plays as PCM, so this is the case
                # that most needs the warning -- and it used to be the one case
                # that skipped it, because None is not False.
                msg = (f"{self.fmt} stores no raw PCM, so no region of this file "
                       f"plays as sound. Reinterpreting these bytes will be loud "
                       f"noise. Continue?")
            self.push_screen(YesNoScreen(msg),
                             lambda ok: ok and self._do_play(off, length))
            return
        self._do_play(off, length)

    def _chunk_name_at(self, off):
        for c in self.chunks:
            base = c.get("offset") or 0
            if base <= off < base + (c.get("size") or 0) + 8:
                return f"'{str(c.get('id', '?')).strip()}'"
        return "this region"

    def _do_play(self, off, length):
        data = _read(self.work, off, min(length, 4 * 1024 * 1024))
        rate, ch, bits, floating = self._audio_params()
        self.action_stop_play(quiet=True)
        self._play = play.play_bytes(data, rate=rate, ch=ch, bits=bits, floating=floating)
        secs = len(data) / max(1, rate * ch * (bits // 8))
        self.notify(f"playing {len(data):,} bytes as {rate} Hz {ch}ch {bits}-bit "
                    f"(~{secs:.1f}s) -- . to stop")

    def action_stop_play(self, quiet=False):
        if getattr(self, "_play", None):
            play.stop(self._play)
            self._play = None
        elif not quiet:
            self.notify("nothing is playing (p plays the selected region)")

    # bounds for a fmt/COMM chunk we are willing to believe. a corrupt header
    # yields arbitrary integers, and they end up in a WAV header whose byte_rate
    # field is a u32 -- so an unclamped rate overflows struct.pack and takes the
    # player down. anything outside these ranges is garbage, not exotic audio.
    _RATE_RANGE = (1000, 768000)          # 768 kHz covers the most extreme hi-res
    _CH_RANGE = (1, 64)
    _BITS_VALID = (8, 16, 24, 32, 64)

    def _audio_params(self):
        """(rate, channels, bits, floating) from the file's fmt/COMM chunk, or
        sensible defaults for reinterpreting arbitrary bytes as PCM. Values a
        corrupt header cannot be telling the truth about fall back to the
        default rather than propagating into the playback WAV header."""
        rate, ch, bits, floating = 44100, 1, 16, False
        for c in self.chunks:
            if str(c.get("id", "")).strip() not in ("fmt", "COMM"):
                continue
            for f in c.get("fields", []):
                n, v = f.get("name", ""), f.get("value")
                try:
                    if n == "sample_rate":
                        lo, hi = self._RATE_RANGE
                        rate = int(v) if lo <= int(v) <= hi else rate
                    elif n in ("channels", "num_channels"):
                        lo, hi = self._CH_RANGE
                        ch = int(v) if lo <= int(v) <= hi else ch
                    elif n == "bits_per_sample":
                        bits = int(v) if int(v) in self._BITS_VALID else bits
                    elif n == "format_tag" and "float" in str(f.get("note", "")).lower():
                        floating = True
                except (ValueError, TypeError):
                    pass
            break
        return rate, ch, bits, floating

    def action_more_rows(self):
        """Raise this chunk's row budget and reload (+).

        The cap exists so a 100k-event file does not build 100k widgets, but a
        row that is counted and unreachable is a part of the file you cannot
        walk to. Selecting the "... N more rows" line and pressing + lifts the
        budget for that chunk only.
        """
        tree = self.query_one("#tree")
        node = getattr(tree, "cursor_node", None)
        if node is not None and id(node) in getattr(self, "_morechunks", ()):
            self._chunkbudget = getattr(self, "_chunkbudget", _CHUNK_CAP) + _CHUNK_CAP
            total = len(self.chunks)
            self._load()
            self.notify(f"showing {min(self._chunkbudget, total):,} of {total:,} chunks")
            return
        idx = self._morerows.get(id(node)) if node is not None else None
        if idx is None:
            self.notify("select a '... more rows' or '... more chunks' line first",
                        severity="warning")
            return
        self._rowbudget[idx] = self._rowbudget.get(idx, _ROW_CAP) + _ROW_CAP
        total = len(self.chunks[idx].get("rows") or [])
        self._load()          # rebuilds the tree; the walk is cached-cheap here
        shown = min(self._rowbudget[idx], total)
        self.notify(f"showing {shown:,} of {total:,} rows")

    def action_help(self):
        self.push_screen(HelpScreen())

    # ── navigation: goto-offset, search, jump-to-finding ──────────────

    def _select_node(self, node):
        """Move the tree cursor to `node`, expanding its parents, and refresh the
        detail/hex panes -- the shared landing used by goto/search/finding."""
        tree = self.query_one("#tree", Tree)
        p = node.parent
        while p is not None:
            if not p.is_expanded:
                p.expand()
            p = p.parent
        self._cur_node = node
        self.call_after_refresh(tree.move_cursor, node)
        data = self._nodemeta.get(id(node))
        if data:
            off, length, accent = data
            self._show(off, length, accent, self._node_name(node),
                       self._edit_hint(node, off, length))

    def _node_containing(self, offset):
        """The most specific tree node whose byte range covers `offset` (a field
        beats its enclosing chunk), or None. Ties break to the smallest range."""
        best = None
        for node, off, length in self._allnodes:
            if length and off <= offset < off + length:
                if best is None or length < best[1]:
                    best = (node, length)
        return best[0] if best else None

    def _jump_to_offset(self, offset, hlen=1, label=""):
        """Land on `offset`: select the node that contains it if any, and show
        the hex there. Used by goto and byte-search hits."""
        node = self._node_containing(offset)
        if node is not None:
            self._select_node(node)
        acc = PEND
        name = label or (self._node_name(node) if node else f"offset 0x{offset:08x}")
        self._show(offset, hlen, acc, name,
                   "" if node else "no chunk covers this offset")

    def _arm_prompt(self, kind, title, initial=""):
        """Reuse #editbar as a one-line prompt (goto/search). Distinct from a
        field edit: on_input_submitted routes on self._prompt first."""
        if self._edit_target:
            self.action_cancel_edit()
        self._prompt = {"kind": kind}
        bar = self.query_one("#editbar", Input)
        bar.value = initial
        bar.remove_class("hidden")
        bar.border_title = title
        bar.focus()

    def action_goto(self):
        self._arm_prompt("goto", "goto offset (0x.. or decimal)  enter  esc")

    def action_search(self):
        self._arm_prompt(
            "search",
            "search: text=fuzzy name/value, 0x..=hex bytes, \"..\"=ascii  n/N cycle")

    def _run_goto(self, text):
        text = text.strip()
        if not text:
            return
        try:
            offset = int(text, 0)
        except ValueError:
            self.notify(f"not an offset: {text!r}", severity="error")
            return
        if not (0 <= offset < self.fsize):
            self.notify(f"offset 0x{offset:x} outside the file (0..{self.fsize:,})",
                        severity="error")
            return
        self._jump_to_offset(offset, 1, f"goto 0x{offset:08x}")

    def _run_search(self, text):
        text = text.strip()
        if not text:
            return
        needle = self._search_needle(text)
        if needle is not None:                       # raw-byte search
            with open(self.work, "rb") as f:
                data = f.read()
            # Count every occurrence; keep the first _SEARCH_CAP for cycling.
            # data.find is memchr-class C, so counting the tail costs far less
            # than the widget work already done, and it is the difference
            # between "4096 match(es)" and the truth.
            hits, total, pos = [], 0, data.find(needle)
            while pos != -1:
                total += 1
                if len(hits) < _SEARCH_CAP:
                    hits.append(("byte", pos, len(needle)))
                pos = data.find(needle, pos + 1)
            desc = f"{len(needle)} byte(s)"
        else:                                        # fuzzy name/value search
            hits = [("node", n) for n, _o, _l in self._allnodes
                    if _fuzzy(text, self._node_name(n))]
            total = len(hits)                        # the tree is already in hand
            desc = f"'{text}'"
        if not hits:
            self.notify(f"no match for {desc}", severity="warning")
            self._search = None
            return
        self._search = {"desc": desc, "hits": hits, "idx": -1, "total": total}
        # house shape: the true total, then what was actually listed
        more = (f"; first {len(hits):,} of {total:,} reachable"
                if total > len(hits) else "")
        self.notify(f"{total:,} match(es) for {desc}{more}; n/N to cycle")
        self._search_step(1)

    @staticmethod
    def _search_needle(text):
        """Bytes to search for, or None if `text` is a fuzzy (name/value) query.
        0x.. / bare even-length hex -> those bytes; "..'/'.." -> ascii bytes."""
        t = text.strip()
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
            return t[1:-1].encode("utf-8", "replace")
        h = t[2:] if t[:2].lower() == "0x" else t
        h = h.replace(" ", "")
        if t[:2].lower() == "0x" or (len(h) >= 2 and len(h) % 2 == 0
                                     and all(c in "0123456789abcdefABCDEF" for c in h)):
            try:
                return bytes.fromhex(h)
            except ValueError:
                return None
        return None

    def _search_step(self, direction):
        s = self._search
        if not s or not s["hits"]:
            self.notify("no active search (press / to search)")
            return
        s["idx"] = (s["idx"] + direction) % len(s["hits"])
        hit = s["hits"][s["idx"]]
        # "1/4096" reads as the total. A "+" says the cycle is a prefix of it.
        total = s.get("total", len(s["hits"]))
        pos = (f"{s['idx'] + 1}/{len(s['hits'])}"
               + ("+" if total > len(s["hits"]) else ""))
        if hit[0] == "byte":
            self._jump_to_offset(hit[1], hit[2],
                                 f"match {pos} @ 0x{hit[1]:08x}  ({s['desc']})")
        else:
            self._select_node(hit[1])
            self.notify(f"match {pos}  {s['desc']}")

    def action_search_next(self):
        self._search_step(1)

    def action_search_prev(self):
        self._search_step(-1)

    def action_next_finding(self):
        if not self.findings:
            self.notify("no forensics findings")
            return
        self._finding_idx = (self._finding_idx + 1) % len(self.findings)
        f = self.findings[self._finding_idx]
        off = f.get("offset", 0)
        self._jump_to_offset(
            off, 1, f"finding {self._finding_idx + 1}/{len(self.findings)}: "
            f"{f.get('message', '')[:60]}")
        self._render_anomalies()

    def action_yank(self):
        """Copy the selected node's bytes (as hex) to the clipboard -- a common
        forensics move (paste an interesting region into another tool)."""
        node = self._cur_node
        data = self._nodemeta.get(id(node)) if node else None
        if not data or data[0] is None or not data[1]:
            self.notify("nothing to yank (highlight a field/chunk)", severity="warning")
            return
        off, length, _ = data
        blob = _read(self.work, off, min(length, _HEX_CAP))
        hexs = blob.hex(" ")
        try:
            self.copy_to_clipboard(hexs)
            where = "clipboard"
        except Exception:
            where = "(clipboard unavailable)"
        note = f", capped at {_HEX_CAP}" if length > _HEX_CAP else ""
        self.notify(f"yanked {len(blob)} bytes as hex -> {where}{note}")

    def _pending_changes(self):
        """(regions, src_len, work_len, total): changed byte regions between the
        working copy and the saved original, each (offset, old_bytes, new_bytes).

        `total` is how many regions EXIST; `regions` holds the first _DIFF_CAP of
        them. The scan used to stop at _DIFF_CAP + 1 so the screen could print
        ".. 1 more regions", which made 201 the largest number it could ever
        report: 1,000 changed regions rendered as "201 region(s)". This is the
        screen consulted before ctrl+s, so an accidental thousand-region
        overwrite read as a two-hundred-region one.

        A length change is reported as one region from the first difference (a
        text re-serialization shifts the tail, so per-run diffing is not
        meaningful there)."""
        try:
            with open(self.work, "rb") as f:
                work = f.read()
            with open(self.src, "rb") as f:
                src = f.read()
        except OSError:
            return [], 0, 0, 0
        if len(src) != len(work):
            start, o, n = self._minimal_delta(src, work)
            regions = [(start, o, n)] if o != n else []
            return regions, len(src), len(work), len(regions)
        regions = []
        total = 0
        i = 0
        while i < len(src):
            if src[i] != work[i]:
                j = i
                while j < len(src) and src[j] != work[j]:
                    j += 1
                total += 1
                if len(regions) < _DIFF_CAP:
                    regions.append((i, src[i:j], work[i:j]))
                i = j
            else:
                i += 1
        return regions, len(src), len(work), total

    def action_diff(self):
        if not self.work:
            return
        regions, sl, wl, total = self._pending_changes()
        self.push_screen(DiffScreen(regions, sl, wl, total))

    def _byte_map(self):
        """(segments, unaccounted): the file's top-level byte regions biggest
        first, each (id, offset, size, pct, accent). Excludes the whole-file
        container and any chunk nested inside another (e.g. SF2 samples inside
        smpl), so the map answers 'where do the bytes go' at the top level."""
        cand = [(c["id"], c["offset"], c["size"], PALETTE[i % len(PALETTE)])
                for i, c in enumerate(self.chunks)
                if isinstance(c.get("offset"), int) and isinstance(c.get("size"), int)
                and c["size"] > 0 and not (c["offset"] == 0 and c["size"] >= self.fsize)]

        def nested(off, size):
            return any(o <= off and off + size <= o + s and s > size
                       for _i, o, s, _a in cand)
        top = [(cid, off, size, _a) for cid, off, size, _a in cand
               if not nested(off, size)]
        top.sort(key=lambda x: -x[2])
        fsize = max(1, self.fsize)
        segs = [(str(cid).strip()[:8], off, size, size / fsize * 100, a)
                for cid, off, size, a in top]
        unaccounted = max(0, self.fsize - sum(s for _c, _o, s, _a in top))
        return segs, unaccounted

    def action_map(self):
        if not self.chunks:
            # `m` is a shown footer binding, so returning in silence made it
            # indistinguishable from a broken build on exactly the files a
            # person opens the TUI for -- the unrecognised ones.
            self.notify(f"no byte map: nothing was parsed out of this file "
                        f"({self.fmt})", severity="warning")
            return
        segs, un = self._byte_map()
        self.push_screen(MapScreen(segs, self.fsize, un))

    def action_validate(self):
        """Show the constraint model's violations for the working copy; from the
        panel, `r` applies the witnessed repairs (still unsaved)."""
        if not self.work:
            self.notify("open a file first (o)", severity="warning")
            return
        from acidcat.core.write import constraints
        with open(self.work, "rb") as f:
            data = f.read()
        report = constraints.analyze(data)
        if report is None:
            self.notify("not a structurally-modeled container (WAV/AIFF/MP4/...)",
                        severity="warning")
            return

        def after(result):
            if result == "repair":
                self._do_repair()
        self.push_screen(ValidateScreen(report), after)

    def _do_repair(self):
        from acidcat.core.write import constraints
        from acidcat.core.write.repairers import AudioGuardError
        with open(self.work, "rb") as f:
            data = f.read()
        try:
            new_data, report = constraints.repair(data)
        except AudioGuardError as e:
            self.notify(f"repair refused: {e}", severity="error")
            return
        if not report.repairable:
            self.notify("nothing witnessed to repair")
            return
        self._apply_to_work(new_data)
        self.notify(f"repaired {len(report.repairable)} field(s) "
                    f"(unsaved -- ctrl+s to save)")

    def action_follow_xref(self):
        """Follow the selected field's pointer (its `xref` absolute offset) to
        where it points, flagging a dangling (out-of-bounds) one -- a real
        forensic tell as well as a navigation aid."""
        node = self._cur_node
        target = self._xref.get(id(node)) if node else None
        if target is None:
            self.notify("this field is not a pointer (no xref)", severity="warning")
            return
        if not (0 <= target < self.fsize):
            self.notify(f"DANGLING pointer -> 0x{target:x} is outside the file "
                        f"(0..0x{self.fsize:x})", severity="error")
            return
        self._jump_to_offset(target, 1, f"followed pointer -> 0x{target:08x}")

    def on_tree_node_highlighted(self, event):
        self._cur_node = event.node
        if self._edit_target:            # moving off the field cancels an edit
            self.action_cancel_edit()
        self._hexedit = None             # ditto an abandoned in-pane hex edit
        data = self._nodemeta.get(id(event.node))
        if not data:
            return
        off, length, accent = data
        hint = self._edit_hint(event.node, off, length)
        xref = self._xref.get(id(event.node))
        if xref is not None:
            danger = "" if 0 <= xref < self.fsize else " (DANGLING, out of bounds)"
            ptr = f"pointer -> 0x{xref:08x}{danger} -- press x to follow"
            hint = f"{hint}\n{ptr}" if hint else ptr
        spans = None
        key = self._nodekey.get(id(event.node))
        if key and key[0] == "chunk":                # tint each field within the chunk
            c = self.chunks[key[1]]
            spans = [(_field_abs(c, fl), fl.get("len") or 0)
                     for fl in c.get("fields", [])]
            spans = [(a, ln) for a, ln in spans if a is not None and ln]
        self._show(off, length, accent, self._node_name(event.node), hint, spans)

    def _edit_hint(self, node, off, length):
        """A short note in the detail pane telling the user how the highlighted
        field can be edited (value / enum / hex / text), so it's discoverable."""
        if off is None or not length:
            return ""
        if id(node) in self._textfield:
            return f"text-editable ({self._textfield[id(node)]}) -- press e"
        value, enc, raw = self._editval.get(id(node), (None, None, None))
        rb = _read(self.work, off, length)
        if enc is not None:
            bt = self._bit_target(off, value, enc)
            if bt is not None:
                if bt["mode"] == "bitfield":
                    return (f"value-editable ({bt['width']}-bit packed) -- "
                            "press e, or ctrl+e for hex")
                return "enum-editable -- press e, or ctrl+e for hex"
            try:
                if encode_value(enc, str(raw if raw is not None else value)) == rb:
                    return f"value-editable ({enc}) -- press e, or ctrl+e for hex"
            except (ValueError, struct.error):
                pass
        if infer_enc(value, rb, self._prefer_be) is not None:
            return "value-editable -- press e, or ctrl+e for hex"
        if length <= _HEXEDIT_CAP:
            return "hex-editable -- press ctrl+e"
        return ""

    # ── inline byte / value editor with live hex preview ──────────────

    def _bit_target(self, off, value, enc):
        """If enc is a bits/bitsmap/bitsdyn annotation, verify it against the
        working copy (the declared bits must decode to the displayed value; a
        wrong annotation must never write blind) and return the edit-target
        fields for it. None means not a bit annotation, or it did not verify --
        the caller falls back to plain value/hex editing."""
        for parse in (parse_bitsmap, parse_bitsdyn, parse_bitfield):
            p = parse(enc)
            if p is None:
                continue
            delta, clen, bitpos, width, extra = p
            cont_off = off + delta
            cur = _read(self.work, cont_off, clen)
            if len(cur) != clen or clen * 8 - bitpos - width < 0:
                return None
            tgt = {"off": cont_off, "length": clen, "fmt": None,
                   "bitpos": bitpos, "width": width}
            if parse is parse_bitsmap:
                ok = _BITMAPS.get(extra, {}).get(
                    bitfield_extract(cur, bitpos, width, 0)) == value
                tgt.update(mode="bitsmap", mapid=extra)
            elif parse is parse_bitsdyn:
                ok = _DYNMAPS[extra](cur).get(
                    bitfield_extract(cur, bitpos, width, 0)) == value
                tgt.update(mode="bitsdyn", dynid=extra)
            else:
                ok = bitfield_extract(cur, bitpos, width, extra) == value
                tgt.update(mode="bitfield", bias=extra)
            return tgt if ok else None
        return None

    def _arm_edit(self, target, initial):
        """Activate the edit bar for `target` with `initial` as the text."""
        self._edit_target = target
        bar = self.query_one("#editbar", Input)
        bar.value = initial
        bar.remove_class("hidden")
        self._update_edit_title()
        bar.focus()
        self._render_preview()

    def action_edit_field(self):
        self._view = "hex"
        node = self._cur_node
        data = self._nodemeta.get(id(node)) if node else None
        if not data:
            self.notify("highlight a field first", severity="warning")
            return
        off, length, accent = data
        if off is None or not length:
            self.notify("this node has no editable byte range", severity="warning")
            return
        name = self._node_name(node)
        # variable-length text field: edit as text through the metadata engine,
        # which re-serializes the chunk so a longer/shorter value is valid.
        mf = self._textfield.get(id(node))
        if mf is not None:
            value = self._editval.get(id(node), (None,))[0]
            self._arm_edit({"off": off, "length": length, "name": name,
                            "mode": "text", "fmt": None, "metafield": mf,
                            "accent": accent},
                           str(value) if value is not None else "")
            return
        if length > _HEXEDIT_CAP:
            self.notify(f"region too large to edit ({length:,} bytes); pick a field",
                        severity="warning")
            return
        raw_bytes = _read(self.work, off, length)
        value, enc, raw_val = self._editval.get(id(node), (None, None, None))
        # bit-packed / enum field: read-modify-write inside its container bytes
        # so neighbouring bit-fields survive. Only if the annotation verifies
        # against the working copy; else fall through to plain value/hex.
        if enc is not None:
            bt = self._bit_target(off, value, enc)
            if bt is not None:
                self._arm_edit({**bt, "name": name, "accent": accent}, str(value))
                return
        fmt = initial = None
        # 1) trust the walker's declared encoding ONLY if it reproduces the
        #    current bytes -- a wrong annotation must never write blind.
        if enc is not None:
            cand = raw_val if raw_val is not None else value
            try:
                if encode_value(enc, str(cand)) == raw_bytes:
                    fmt, initial = enc, str(cand)
            except (ValueError, struct.error):
                pass
        # 2) else infer the layout by round-tripping the displayed value.
        if fmt is None:
            fmt = infer_enc(value, raw_bytes, self._prefer_be)
            if fmt is not None:
                initial = str(value)
        # 3) else raw hex.
        if fmt is not None:
            mode = "value"
        else:
            mode, initial = "hex", raw_bytes.hex(" ")
        self._arm_edit({"off": off, "length": length, "name": name,
                        "mode": mode, "fmt": fmt, "accent": accent}, initial)

    def _update_edit_title(self):
        tgt = self._edit_target
        bar = self.query_one("#editbar", Input)
        if tgt["mode"] == "value":
            kind = f"value ({tgt['fmt']})"
        elif tgt["mode"] == "bitsmap":
            vals = list(_BITMAPS.get(tgt["mapid"], {}).values())
            kind = (f"enum ({tgt['mapid']}, {len(vals)} options)" if len(vals) > 8
                    else "enum: " + " | ".join(str(v) for v in vals))
        elif tgt["mode"] == "bitsdyn":
            cur = _read(self.work, tgt["off"], tgt["length"])
            opts = " | ".join(str(v) for v in _DYNMAPS[tgt["dynid"]](cur).values())
            kind = f"enum: {opts}"
        elif tgt["mode"] == "bitfield":
            kind = f"value ({tgt['width']}-bit packed field)"
        elif tgt["mode"] == "text":
            kind = f"text -> {tgt['metafield']} (variable length)"
        else:
            kind = f"raw hex ({tgt['length']}B)"
        toggle = "  ctrl+t=toggle" if tgt["fmt"] else ""
        bar.border_title = (f"edit {tgt['name']} @ 0x{tgt['off']:08x}  {kind}"
                            f"  enter=write  esc=cancel{toggle}")

    def action_toggle_mode(self):
        """Flip the active field edit between value and raw-hex. Only offered
        when the field has a known value encoding (fmt); a hex-only field stays
        hex. Converts the bar's current text to the other representation so the
        live preview stays consistent."""
        tgt = self._edit_target
        if not tgt:
            self.notify("no field is being edited (e edits the selected field)")
            return
        if tgt["fmt"] is None:
            # enum/packed fields (bitsmap/bitsdyn/bitfield) are value-editable
            # by label or index in place; they just have no fmt to flip to hex
            if tgt.get("mode") in ("bitsmap", "bitsdyn", "bitfield"):
                self.notify("enum/packed field: edit the value in place "
                            "(no separate hex mode)", severity="warning")
            else:
                self.notify("this field is hex-only (no known value encoding)",
                            severity="warning")
            return
        bar = self.query_one("#editbar", Input)
        if tgt["mode"] == "value":
            try:                                  # value -> its bytes as hex
                bar.value = encode_value(tgt["fmt"], bar.value.strip()).hex(" ")
            except (ValueError, struct.error):
                pass                              # keep text; preview flags invalid
            tgt["mode"] = "hex"
        else:
            try:                                  # bytes -> decoded value
                b = bytes.fromhex(bar.value.replace(" ", ""))
                if len(b) == enc_size(tgt["fmt"]):
                    bar.value = str(decode_value(tgt["fmt"], b))
            except (ValueError, struct.error):
                pass
            tgt["mode"] = "value"
        self._update_edit_title()
        self._render_preview()

    def _patch_from_input(self, text):
        """Turn the current editbar text into bytes for the field's byte range, or
        None if invalid/incomplete."""
        tgt = self._edit_target
        try:
            if tgt["mode"] == "bitsmap":
                rawv = resolve_bitsmap(tgt["mapid"], text)
                cur = _read(self.work, tgt["off"], tgt["length"])
                if rawv is None or len(cur) != tgt["length"]:
                    return None
                return bitfield_apply(cur, tgt["bitpos"], tgt["width"], 0, rawv)
            if tgt["mode"] == "bitsdyn":
                cur = _read(self.work, tgt["off"], tgt["length"])
                if len(cur) != tgt["length"]:
                    return None
                rawv = _resolve_in_map(_DYNMAPS[tgt["dynid"]](cur), text)
                if rawv is None:
                    return None
                return bitfield_apply(cur, tgt["bitpos"], tgt["width"], 0, rawv)
            if tgt["mode"] == "bitfield":
                cur = _read(self.work, tgt["off"], tgt["length"])
                if len(cur) != tgt["length"]:
                    return None
                return bitfield_apply(cur, tgt["bitpos"], tgt["width"],
                                      tgt["bias"], int(text.strip(), 0))
            if tgt["mode"] == "value":
                patch = encode_value(tgt["fmt"], text.strip())
            else:
                patch = bytes.fromhex(text.replace(" ", "").replace("\n", ""))
        except (ValueError, struct.error):
            return None
        return patch if len(patch) == tgt["length"] else None

    def _render_preview(self):
        tgt = self._edit_target
        if not tgt:
            return
        if tgt["mode"] == "text":
            d = Text()
            d.append(f"editing {tgt['name']} ", style=f"bold {PEND}")
            d.append(f"as text -> {tgt['metafield']}; re-serialized on write "
                     f"(length may change)", style=SOFT)
            self.query_one("#detail", Static).update(d)
            self.query_one("#hex", Static).update(
                hex_text(self.work, tgt["off"], tgt["length"], PEND,
                         width=self._hex_width()))
            return
        text = self.query_one("#editbar", Input).value
        patch = self._patch_from_input(text)
        detail = self.query_one("#detail", Static)
        d = Text()
        d.append(f"editing {tgt['name']}", style=f"bold {PEND}")
        d.append("   ", style=SOFT)
        d.append("valid, enter to write" if patch is not None
                 else "invalid / wrong length", style=SOFT if patch else SEV["alert"])
        detail.update(d)
        t = Text()
        t.append("preview (unsaved)\n", style=f"bold {PEND}")
        if patch is None:
            _hex_rows(t, tgt["off"], _read(self.work, tgt["off"], tgt["length"]), DIM)
        else:
            _hex_rows(t, tgt["off"], patch, PEND)
        self.query_one("#hex", Static).update(t)

    def on_input_changed(self, event):
        if event.input.id == "editbar" and self._edit_target:
            self._render_preview()

    def on_input_submitted(self, event):
        if event.input.id != "editbar":
            return
        if self._prompt:                     # goto / search prompt, not a field edit
            kind, text = self._prompt["kind"], event.value
            self._end_prompt()
            if kind == "goto":
                self._run_goto(text)
            elif kind == "search":
                self._run_search(text)
            return
        if not self._edit_target:
            return
        tgt = self._edit_target
        if tgt["mode"] == "text":
            try:
                _fmt, new_data, _applied = _write_edit(
                    self.work, {tgt["metafield"]: event.value})
            except (EditError, OSError, ValueError) as e:
                self.notify(f"error: {e}", severity="error")
                return
            self._end_edit()
            self._apply_to_work(new_data)
            self.notify(f"set {tgt['metafield']} (unsaved -- ctrl+s to save)")
            return
        patch = self._patch_from_input(event.value)
        if patch is None:
            self.notify(f"invalid value for a {tgt['length']}-byte field",
                        severity="error")
            return
        try:
            with open(self.work, "rb") as f:
                data = f.read()
            new = data[:tgt["off"]] + patch + data[tgt["off"] + tgt["length"]:]
        except OSError as e:
            self.notify(f"error: {e}", severity="error")
            return
        self._end_edit()
        self._apply_to_work(new)
        self.notify(f"patched {len(patch)} bytes (unsaved -- ctrl+s to save)")

    def _end_prompt(self):
        self._prompt = None
        bar = self.query_one("#editbar", Input)
        bar.value = ""
        bar.add_class("hidden")
        self.query_one("#tree", Tree).focus()

    def action_cancel_edit(self):
        if self._scanning:                   # esc discards a running region scan
            self.action_cancel_scan()
            return
        if self._prompt:                     # esc cancels an armed goto/search prompt
            self._end_prompt()
            return
        if not self._edit_target:
            return
        self._end_edit()
        if self._cur_node:
            data = self._nodemeta.get(id(self._cur_node))
            if data:
                self._show(*data, self._node_name(self._cur_node), "")

    def _end_edit(self):
        self._edit_target = None
        bar = self.query_one("#editbar", Input)
        bar.value = ""
        bar.add_class("hidden")
        self.query_one("#tree", Tree).focus()

    # ── in-pane hex editor: Tab into the hex pane and overwrite bytes ──

    def action_hex_focus(self):
        if len(self.screen_stack) > 1:       # a modal is open; leave Tab to it
            return
        if self._hexedit:                    # already editing; Tab must not
            return                           # restart and drop typed nibbles
        if self._edit_target:
            self.action_cancel_edit()
        node = self._cur_node
        data = self._nodemeta.get(id(node)) if node else None
        if not data:
            self.notify("highlight a field first", severity="warning")
            return
        off, length, _accent = data
        if off is None or not length:
            self.notify("this node has no editable byte range", severity="warning")
            return
        if length > _HEXEDIT_CAP:
            self.notify(f"region too large ({length:,} bytes); pick a field",
                        severity="warning")
            return
        self._view = "hex"
        self._hexedit = {"off": off, "length": length, "cur": 0, "nib": 0,
                         "buf": bytearray(_read(self.work, off, length))}
        self.query_one("#hex", HexPane).focus()
        self._render_hexedit()

    def _exit_hexedit(self):
        self._hexedit = None
        if self._cur_node:
            data = self._nodemeta.get(id(self._cur_node))
            if data:
                self._show(*data, self._node_name(self._cur_node), "")
        self.query_one("#tree", Tree).focus()

    def _hexedit_key(self, event):
        he = self._hexedit
        if he is None:
            return
        k = event.key
        if k == "escape":
            event.stop()
            self._exit_hexedit()
            return
        if k in ("enter", "return"):
            event.stop()
            with open(self.work, "rb") as f:
                data = f.read()
            new = (data[:he["off"]] + bytes(he["buf"])
                   + data[he["off"] + he["length"]:])
            length = he["length"]
            self._hexedit = None
            self._apply_to_work(new)
            self.notify(f"patched {length} bytes (unsaved -- ctrl+s to save)")
            self.query_one("#tree", Tree).focus()
            return
        n = he["length"]
        digit = k.lower()
        if k == "right":
            he["cur"], he["nib"] = min(he["cur"] + 1, n - 1), 0
        elif k == "left":
            he["cur"], he["nib"] = max(he["cur"] - 1, 0), 0
        elif k == "down":
            he["cur"], he["nib"] = min(he["cur"] + 16, n - 1), 0
        elif k == "up":
            he["cur"], he["nib"] = max(he["cur"] - 16, 0), 0
        elif len(digit) == 1 and digit in "0123456789abcdef":
            d, i = int(digit, 16), he["cur"]
            if he["nib"] == 0:
                he["buf"][i] = (he["buf"][i] & 0x0f) | (d << 4)
                he["nib"] = 1
            else:
                he["buf"][i] = (he["buf"][i] & 0xf0) | d
                he["nib"] = 0
                he["cur"] = min(he["cur"] + 1, n - 1)
        else:
            return
        event.stop()
        self._render_hexedit()

    _HEXEDIT_CURSOR = "bold #16181C on #FF4D00"

    def _render_hexedit(self):
        """The edit view is the same grid with one byte styled as the cursor.

        It used to be a hand-inlined fourth copy of the row loop, for one
        reason: `_hex_rows` styled the ascii column independently of `cmap`, so
        there was no way to put the cursor style on both halves of a row. That
        gap is closed, so this is a cmap of exactly one entry.
        """
        he = self._hexedit
        off, buf, cur, nib = he["off"], he["buf"], he["cur"], he["nib"]
        t = Text()
        t.append("HEX EDIT  ", style=f"bold {SEV['alert']}")
        t.append(f"byte {cur + 1}/{len(buf)} @ 0x{off + cur:08x}"
                 f"{' low-nibble' if nib else ''}   arrows move  0-9a-f overwrite"
                 f"  enter=apply  esc=cancel\n", style=DIM)
        _hex_rows(t, off, bytes(buf), ACCENT, {cur: self._HEXEDIT_CURSOR},
                  self._hex_width())
        self.query_one("#hex", Static).update(t)

    # ── other actions ─────────────────────────────────────────────────

    def action_open(self):
        if self.dirty:
            self.push_screen(
                ConfirmScreen("unsaved changes -- save before opening another?"),
                self._resolve_pending(self._browse))
        else:
            self._browse()

    def _browse(self):
        start = os.path.dirname(os.path.abspath(self.src)) if self.src else os.getcwd()

        def after(path):
            if path and os.path.isfile(path):
                self._open_path(path)

        self.push_screen(BrowseScreen(start), after)

    def action_edit(self):
        if not self.work:
            self.notify("open a file first (o)", severity="warning")
            return
        prof = edit_profile(self.work)
        if prof is None:
            self.notify(f"no metadata editor for this format ({self.fmt})",
                        severity="warning")
            return

        def after(result):
            if result and result.get("new_data") is not None:
                self._apply_to_work(result["new_data"])
                n = len(result.get("applied", []))
                self.notify(f"edited {n} field(s) (unsaved -- ctrl+s to save)")

        self.push_screen(EditScreen(self.work, prof[0], prof[1]), after)

    def action_strip(self):
        """Remove every identifying tag (s) -- after asking.

        A single unmodified key that mutates the file the moment it is pressed
        is the wrong shape. Every other one-key binding here reads; the ones
        that write are either modified (ctrl+s, ctrl+e) or open a form you then
        have to fill in (e, w). This was the exception, and a mistyped `s`
        while reaching for anything else silently discarded every tag.
        """
        if not self.work:
            self.notify("open a file first (o)", severity="warning")
            return
        self.push_screen(
            YesNoScreen("Strip ALL identifying metadata from this file? "
                        "The edit is undoable (ctrl+z) and is not written "
                        "until you save.", yes_label="strip"),
            lambda ok: ok and self._do_strip())

    def _do_strip(self):
        try:
            _fmt, new_data, removed = _write_strip(self.work)
        except (EditError, OSError, ValueError) as e:
            self.notify(f"strip failed: {e}", severity="error")
            return
        self._apply_to_work(new_data)
        what = ", ".join(removed) if removed else "nothing to remove"
        self.notify(f"stripped: {what} (unsaved -- ctrl+s to save)")

    @staticmethod
    def _expanded_count(tree):
        n, stack = 0, [tree.root]
        while stack:
            node = stack.pop()
            if node.is_expanded:
                n += 1
                stack.extend(node.children)
        return n

    def action_expand_all(self):
        tree = self.query_one("#tree", Tree)
        before = self._expanded_count(tree)
        tree.root.expand_all()
        self._report_fold(tree, before, "expanded", "already fully expanded")

    def action_collapse_all(self):
        tree = self.query_one("#tree", Tree)
        before = self._expanded_count(tree)
        for node in tree.root.children:
            node.collapse_all()
        self._report_fold(tree, before, "collapsed", "already collapsed")

    def _report_fold(self, tree, before, verb, nothing):
        """Say what happened, including when nothing did.

        The tree opens collapsed, so `c` on a fresh file changed nothing and
        said nothing -- and a key the footer advertises that produces no
        response at all is indistinguishable from a broken build. That is the
        same shape as the bugs found by hand in this pane three times over.
        """
        delta = abs(self._expanded_count(tree) - before)
        self.notify(f"{verb} {delta} node(s)" if delta else nothing)
