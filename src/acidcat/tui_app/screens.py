"""acidcat TUI -- the modal screens and the hex-pane widget.

Self-contained Textual widgets the app pushes onto its screen stack: file
browse, metadata edit, confirm, help, pending-diff, byte map, validate,
carve-regions, disc-extract, and a text prompt, plus the focusable HexPane.
They talk back through callbacks, so nothing here imports the app (no cycle).
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, DirectoryTree, Input, Label, Static

from acidcat.commands.write import _edit as _write_edit
from acidcat.core.write.edits import EditError
from acidcat.tui_app.render import _DIFF_CAP
from acidcat.tui_theme import ACCENT, DIM, PEND, SEV, SOFT


class HexPane(Static):
    """The right-hand hex view. Focusable so it can host in-place hex editing:
    when the app is in hex-edit mode, key events route to the app's handler
    (cursor movement + nibble overwrite); otherwise it behaves as a plain
    read-only pane."""
    can_focus = True

    def on_key(self, event):
        if getattr(self.app, "_hexedit", None):
            self.app._hexedit_key(event)




class BrowseScreen(ModalScreen):
    """A file picker: navigate a directory tree, enter selects, esc cancels.
    dismiss()es with the chosen path string, or None on cancel."""

    CSS = """
    BrowseScreen { align: center middle; }
    #browsebox { width: 80%; height: 80%; border: round #08F9DF;
                 background: #16181C; padding: 1 2; }
    #browsehint { color: #8A9099; padding-bottom: 1; }
    DirectoryTree { background: #16181C; }
    """
    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, start):
        super().__init__()
        self.start = start

    def compose(self) -> ComposeResult:
        with Vertical(id="browsebox"):
            yield Static(Text("open a file  (enter selects, esc cancels)",
                              style=f"bold {ACCENT}"), id="browsehint")
            yield DirectoryTree(self.start, id="dtree")

    def on_directory_tree_file_selected(self, event):
        self.dismiss(str(event.path))

    def action_cancel(self):
        self.dismiss(None)


class EditScreen(ModalScreen):
    """The exiftool-style metadata editor: the write engine's supported fields
    for this format. Blank inputs are left unchanged; typed values are applied
    via commands.write._edit + core.writer.commit (atomic, leaves a _original
    backup). dismiss()es with a result dict on a successful write, else None."""

    CSS = """
    EditScreen { align: center middle; }
    #editbox { width: 72; height: auto; max-height: 90%; border: round #FF4D00;
               background: #16181C; padding: 1 2; }
    #edittitle { color: #FF4D00; text-style: bold; padding-bottom: 1; }
    #edithint { color: #565B63; padding-bottom: 1; }
    #editstatus { color: #FF4D00; padding-top: 1; }
    EditScreen Label { color: #8A9099; }
    EditScreen Input { margin-bottom: 1; }
    #editbtns { height: auto; padding-top: 1; }
    """
    BINDINGS = [("escape", "cancel", "cancel"), ("ctrl+s", "save", "save")]

    def __init__(self, path, profile, fields):
        super().__init__()
        self.path = path
        self.profile = profile
        self.fields = fields

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="editbox"):
            yield Static(f"edit metadata  [{self.profile}]", id="edittitle")
            yield Static("type to set a field; leave blank to keep current. "
                         "ctrl+s saves (makes a _original backup), esc cancels.",
                         id="edithint")
            for field, label in self.fields:
                yield Label(label)
                yield Input(id=f"f_{field}", placeholder=f"{field} (unchanged)")
            yield Static("", id="editstatus")
            with Horizontal(id="editbtns"):
                yield Button("save", id="save", variant="warning")
                yield Button("cancel", id="cancel")

    def on_button_pressed(self, event):
        if event.button.id == "save":
            self.action_save()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)

    def action_save(self):
        changes = {}
        for field, _ in self.fields:
            val = self.query_one(f"#f_{field}", Input).value.strip()
            if val:
                changes[field] = val
        if not changes:
            self.dismiss(None)
            return
        status = self.query_one("#editstatus", Static)
        try:
            _fmt, new_data, applied = _write_edit(self.path, changes)
        except (EditError, OSError, ValueError) as e:
            status.update(Text(f"error: {e}", style=SEV["alert"]))
            return
        self.dismiss({"new_data": new_data, "applied": applied})


class ConfirmScreen(ModalScreen):
    """Unsaved-changes prompt. dismiss()es with 'save', 'discard', or None
    (cancel)."""

    CSS = """
    ConfirmScreen { align: center middle; }
    #confbox { width: 60; height: auto; border: round #FF4D00;
               background: #16181C; padding: 1 2; }
    #confmsg { color: #C9CDD3; padding-bottom: 1; }
    #confbtns { height: auto; }
    """
    # save and discard were reachable by mouse only, on the one prompt that
    # stands between the user and losing an edit.
    BINDINGS = [
        ("s", "save", "save"),
        ("d", "discard", "discard"),
        ("c", "cancel", "cancel"),
        ("escape", "cancel", "cancel"),
    ]

    def __init__(self, prompt):
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="confbox"):
            yield Static(Text(self.prompt, style=f"bold {PEND}"), id="confmsg")
            with Horizontal(id="confbtns"):
                yield Button("save  (s)", id="save", variant="success")
                yield Button("discard  (d)", id="discard", variant="error")
                yield Button("cancel  (c)", id="cancel")

    def on_mount(self):
        self.query_one("#save", Button).focus()

    def on_button_pressed(self, event):
        self.dismiss(event.button.id if event.button.id != "cancel" else None)

    def action_save(self):
        self.dismiss("save")

    def action_discard(self):
        self.dismiss("discard")

    def action_cancel(self):
        self.dismiss(None)


class HelpScreen(ModalScreen):
    """Key reference overlay. Any of esc / ? closes it."""

    CSS = """
    HelpScreen { align: center middle; }
    #helpbox { width: 74; height: auto; max-height: 90%; border: round #08F9DF;
               background: #16181C; padding: 1 2; }
    """
    BINDINGS = [("escape", "close", "close"), ("question_mark", "close", "close")]

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("acidcat tui  --  keys\n\n", style=f"bold {ACCENT}")
        rows = [
            ("arrows / enter", "move + expand the tree"),
            ("a / c", "expand all / collapse all"),
            ("tab / shift+tab", "move focus between the tree and the hex pane"),
            ("z", "give the focused pane the whole screen (again to restore)"),
            ("g", "goto offset (0x.. or decimal)"),
            ("/", "search: text=fuzzy name/value, 0x..=hex, \"..\"=ascii"),
            ("n / N", "next / previous search match"),
            ("f", "jump to the next forensics finding"),
            ("x", "follow a pointer field to where it points (flags dangling)"),
            ("m", "byte map: where the file's bytes go, biggest regions first"),
            ("b", "byte view: cycle hex / entropy / hilbert / histogram"),
            ("r", "byte view: whole file or just the selected region"),
            ("S", "byte view: vertical scale (entropy 0-8 or auto; "
                  "histogram linear, log, clipped)"),
            ("arrows", "on a focused graph: up/down change the scale, "
                       "left/right move the selection (a region-scoped graph "
                       "follows it live)"),
            ("p", "play the selected region as raw PCM (. stops); needs ffplay"),
            ("v", "validate structure: constraint violations, r to repair them"),
            ("y", "yank the selected bytes as hex to the clipboard"),
            ("d", "review all pending changes (offset old->new) before save"),
            ("e", "edit the selected field (value or hex)"),
            ("ctrl+t", "toggle the edit between value and raw hex"),
            ("ctrl+e", "hex-edit the field in the pane (arrows move, 0-9a-f type)"),
            ("w", "edit tags (metadata form)"),
            ("s", "strip identifying metadata (asks first)"),
            ("ctrl+s", "save to the original (writes a _original backup)"),
            ("ctrl+z / ctrl+r", "undo / redo the last edit"),
            ("o", "open another file"),
            ("l", "locate audio regions in a blob / disk image (auto for a blob)"),
            ("u", "from a region, go back up to the region browser"),
            ("+", "on a '... more rows' line, list more of that chunk's rows"),
            ("esc", "cancel the current edit / prompt"),
            ("q", "quit"),
        ]
        for k, d in rows:
            t.append(f"  {k:16}", style=f"bold {PEND}")
            t.append(f"{d}\n", style=SOFT)
        t.append("\nRegion browser (a blob opens straight into it): enter descends "
                 "into a region as if it were a file, x / e extract one / all, "
                 "m cycles the forensics mode (strict/normal/aggressive), t toggles "
                 "the transform lens (audio hidden under XOR/rotate/nibble-swap), "
                 "c carves an arbitrary offset+length, / searches raw bytes. A big "
                 "image scans in segments with live progress: space pauses/resumes, "
                 "enter keeps what was found so far, esc discards and backs out.",
                 style=DIM)
        t.append("\nDisc audio browser (a PS1 / CD-XA disc image opens into it): the "
                 "game's .STR soundtrack tracks and .VB/.VAG SPU sound banks, from the "
                 "ISO filesystem. enter / p auditions the selected track, x / a extract "
                 "one / all to WAV.", style=DIM)
        t.append("\nEdits go to a temp working copy; nothing touches the original "
                 "until ctrl+s.", style=DIM)
        with Vertical(id="helpbox"):
            yield Static(t)

    def action_close(self):
        self.dismiss(None)


class DiffScreen(ModalScreen):
    """Review pending byte changes (working copy vs the original) before a save.
    Any of esc / d closes it.

    The count is of every region that exists; the LIST below it stops at
    _DIFF_CAP. Those were the same number until 1,000 changes reported as 201,
    on the one screen a person consults before overwriting their file."""

    CSS = """
    DiffScreen { align: center middle; }
    #diffbox { width: 82; height: auto; max-height: 90%; border: round #FF4D00;
               background: #16181C; padding: 1 2; }
    """
    BINDINGS = [("escape", "close", "close"), ("d", "close", "close")]

    def __init__(self, regions, src_len, work_len, total=None):
        super().__init__()
        self.regions = regions
        self.src_len = src_len
        self.work_len = work_len
        # how many regions EXIST, versus the prefix held in `regions`. Defaults
        # to len(regions) so an older two-arg caller still reads correctly.
        self.total = len(regions) if total is None else total

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("pending changes  ", style=f"bold {ACCENT}")
        if self.src_len != self.work_len:
            t.append(f"(file size {self.src_len:,} -> {self.work_len:,} bytes)\n",
                     style=SOFT)
        elif not self.regions:
            t.append("none -- working copy matches the original\n", style=SOFT)
        else:
            shown = ("" if self.total <= len(self.regions)
                     else f"; listing the first {len(self.regions):,}")
            t.append(f"{self.total:,} region(s) vs the original{shown}\n",
                     style=SOFT)
        for off, old, new in self.regions[:_DIFF_CAP]:
            t.append(f"\n0x{off:08x}  ", style=f"bold {PEND}")
            t.append(f"{len(old)}B\n", style=DIM)
            t.append("  old ", style=SOFT)
            t.append(old[:24].hex(" ") + (" .." if len(old) > 24 else ""), style=DIM)
            t.append("\n  new ", style=SOFT)
            t.append(new[:24].hex(" ") + (" .." if len(new) > 24 else ""),
                     style=PEND)
            t.append("\n")
        if self.total > len(self.regions):
            t.append(f"\n.. {self.total - len(self.regions):,} more regions\n",
                     style=DIM)
        t.append("\nctrl+s to save, esc to keep editing.", style=DIM)
        with Vertical(id="diffbox"):
            yield Static(t)

    def action_close(self):
        self.dismiss(None)


class MapScreen(ModalScreen):
    """A byte-budget map: where the file's bytes actually go, top-level regions
    biggest first with a proportional bar. Any of esc / m closes it."""

    CSS = """
    MapScreen { align: center middle; }
    #mapbox { width: 86; height: auto; max-height: 90%; border: round #08F9DF;
              background: #16181C; padding: 1 2; }
    """
    BINDINGS = [("escape", "close", "close"), ("m", "close", "close")]

    def __init__(self, segments, fsize, unaccounted):
        super().__init__()
        self.segments = segments
        self.fsize = fsize
        self.unaccounted = unaccounted

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("byte map  ", style=f"bold {ACCENT}")
        t.append(f"{self.fsize:,} bytes, {len(self.segments)} top-level region(s)\n",
                 style=SOFT)
        for i, (cid, off, size, pct, accent) in enumerate(self.segments):
            bar = "#" * max(1, round(pct / 100 * 40)) if size else ""
            t.append(f"\n0x{off:08x}  ", style=DIM)
            t.append(f"{cid:<8}", style=f"bold {accent}")
            t.append(f"{size:>12,}  {pct:5.1f}%\n", style=SOFT)
            t.append("  " + bar + "\n", style=accent)
        if self.unaccounted > 0:
            t.append(f"\n{self.unaccounted:,} bytes unaccounted "
                     f"({self.unaccounted / self.fsize * 100:.1f}%): gaps, chunk "
                     f"headers, or trailing data\n", style=f"bold {SEV['warn']}")
        t.append("\nesc / m to close.", style=DIM)
        with Vertical(id="mapbox"):
            yield Static(t)

    def action_close(self):
        self.dismiss(None)


class ValidateScreen(ModalScreen):
    """The constraint model's read-only face inside the TUI: the derived-field
    violations of the working copy, each with the witness that makes it fixable.
    `r` applies the witnessed repairs to the working copy (still unsaved); esc /
    v close."""

    CSS = """
    ValidateScreen { align: center middle; }
    #valbox { width: 86; height: auto; max-height: 90%; border: round #08F9DF;
              background: #16181C; padding: 1 2; }
    """
    BINDINGS = [("escape", "close", "close"), ("v", "close", "close"),
                ("r", "repair", "repair")]

    def __init__(self, report):
        super().__init__()
        self.report = report

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("validate  ", style=f"bold {ACCENT}")
        t.append(f"[{self.report.label}]\n", style=SOFT)
        vios = self.report.violations
        fixable = self.report.repairable
        self._can_repair = bool(fixable)
        if not vios:
            t.append("\nstructurally consistent -- every derived field matches "
                     "its function.\n", style=f"bold {SEV['notice']}")
        else:
            t.append(f"\n{len(vios)} violation(s), {len(fixable)} witnessed\n",
                     style=SOFT)
            for v in vios:
                t.append(f"\n  {v.kind:<7}", style=f"bold {ACCENT}")
                t.append(f"{v.describe()}\n", style=SOFT)
                wit = f"witness: {v.witness}" if v.witness else "no witness -- left as-is"
                t.append(f"          {wit}\n", style=DIM)
        if self._can_repair:
            t.append(f"\nr to repair {len(fixable)} witnessed field(s) "
                     f"(unsaved), esc / v to close.", style=DIM)
        else:
            t.append("\nesc / v to close.", style=DIM)
        with Vertical(id="valbox"):
            yield Static(t)

    def action_repair(self):
        self.dismiss("repair" if getattr(self, "_can_repair", False) else None)

    def action_close(self):
        self.dismiss(None)


class RegionsScreen(ModalScreen):
    """The blob region browser: the `locate` results as a navigable table.
    enter descends into the selected region (opened as if it were a standalone
    file), x extracts it, e extracts every region. dismiss()es with a dict
    {action: descend|extract|extract_all, index: row}, or None on esc."""

    CSS = """
    RegionsScreen { align: center middle; }
    #regbox { width: 92%; height: 84%; border: round #08F9DF;
              background: #16181C; padding: 1 2; }
    #reghint { color: #8A9099; padding-bottom: 1; }
    #regtable { height: 1fr; }
    DataTable { background: #16181C; }
    """
    BINDINGS = [
        ("x", "extract", "extract one"),
        ("e", "extract_all", "extract all"),
        ("m", "mode", "cycle mode"),
        ("t", "transforms", "transform lens"),
        ("c", "carve", "manual carve"),
        ("slash", "search", "byte search"),
        ("escape", "cancel", "back"),
    ]

    def __init__(self, regions, blob_name, mode="normal", transforms=False):
        super().__init__()
        self.regions = regions
        self.blob_name = blob_name
        self.mode = mode
        self.transforms = transforms

    def compose(self) -> ComposeResult:
        with Vertical(id="regbox"):
            nc = sum(1 for r in self.regions if r["kind"] == "container")
            nt = sum(1 for r in self.regions if r["kind"] == "transformed")
            nb = len(self.regions) - nc - nt
            lens = "  lens:ON" if self.transforms else ""
            yield Static(
                Text(f"{self.blob_name}  --  {len(self.regions)} region(s): "
                     f"{nc} container / {nb} blob"
                     + (f" / {nt} transformed" if nt else "")
                     + f"   [mode:{self.mode}{lens}]", style=f"bold {ACCENT}"),
                id="reghint")
            yield Static(
                Text("enter descend   x/e extract one/all   m mode   "
                     "t transform-lens   c carve range   / byte-search   esc back",
                     style=SOFT), id="regkeys")
            # populate before yield so the table never depends on post-mount
            # query timing (which was flaky for a re-pushed screen)
            t = DataTable(id="regtable")
            t.cursor_type = "row"
            t.zebra_stripes = True
            t.add_columns("#", "offset", "end", "kind", "format", "conf", "length", "geometry")
            for i, r in enumerate(self.regions):
                geo = r.get("geometry") or {}
                gs = ""
                if geo:
                    ch = "stereo" if geo.get("channels") == 2 else "mono"
                    gs = (f"float{geo['width']}" if geo.get("float")
                          else f"{geo.get('endian') or '?'}-{geo.get('width')}bit") + f" {ch}"
                fmt = (r.get("transform") or r.get("format")
                       or (r.get("probe") or {}).get("top") or "raw-pcm")
                t.add_row(str(i), f"0x{r['offset']:08x}", f"0x{r['end']:08x}",
                          r["kind"], fmt,
                          f"{r['confidence']:.2f}", f"{r['length']:,}", gs)
            yield t

    def on_mount(self):
        try:
            self.query_one("#regtable", DataTable).focus()
        except Exception:
            pass

    def on_data_table_row_selected(self, event):
        self.dismiss({"action": "descend", "index": event.cursor_row})

    def _cursor(self):
        return self.query_one("#regtable", DataTable).cursor_row

    def action_extract(self):
        self.dismiss({"action": "extract", "index": self._cursor()})

    def action_extract_all(self):
        self.dismiss({"action": "extract_all", "index": -1})

    def action_mode(self):
        nxt = {"strict": "normal", "normal": "aggressive",
               "aggressive": "strict"}[self.mode]
        self.dismiss({"action": "rescan", "mode": nxt, "transforms": self.transforms})

    def action_transforms(self):
        self.dismiss({"action": "rescan", "mode": self.mode,
                      "transforms": not self.transforms})

    def action_carve(self):
        self.dismiss({"action": "carve"})

    def action_search(self):
        self.dismiss({"action": "search"})

    def action_cancel(self):
        self.dismiss(None)


class DiscScreen(ModalScreen):
    """CD-XA disc audio browser: the game's .STR soundtrack tracks and .VB/.VAG
    SPU sound banks, from the ISO 9660 filesystem. enter/p auditions the selected
    entry, x/a extract one/all. dismiss()es with {action: play|extract|extract_all,
    index} or None on esc."""

    CSS = """
    DiscScreen { align: center middle; }
    #discbox { width: 92%; height: 84%; border: round #08F9DF;
               background: #16181C; padding: 1 2; }
    #dischint { color: #8A9099; padding-bottom: 1; }
    #disctable { height: 1fr; }
    DataTable { background: #16181C; }
    """
    BINDINGS = [
        ("x", "extract", "extract one"),
        ("a", "extract_all", "extract all"),
        ("p", "play", "audition"),
        ("escape", "cancel", "back"),
    ]
    _KIND = {"XA": "soundtrack (XA)", "VB": "sound bank (SPU)", "VAG": "sample (SPU)"}

    def __init__(self, entries, disc_name):
        super().__init__()
        self.entries = entries
        self.disc_name = disc_name

    def compose(self) -> ComposeResult:
        with Vertical(id="discbox"):
            nx = sum(1 for e in self.entries if e["kind"] == "XA")
            nb = sum(1 for e in self.entries if e["kind"] in ("VB", "VAG"))
            yield Static(
                Text(f"{self.disc_name}  --  {len(self.entries)} audio file(s): "
                     f"{nx} soundtrack / {nb} sound bank", style=f"bold {ACCENT}"),
                id="dischint")
            yield Static(Text("enter/p audition   x/a extract one/all   esc back",
                              style=SOFT), id="disckeys")
            t = DataTable(id="disctable")
            t.cursor_type = "row"
            t.zebra_stripes = True
            t.add_columns("#", "name", "kind", "size")
            for i, e in enumerate(self.entries):
                t.add_row(str(i), e["path"], self._KIND.get(e["kind"], e["kind"]),
                          f"{e['size']:,}")
            yield t

    def on_mount(self):
        try:
            self.query_one("#disctable", DataTable).focus()
        except Exception:
            pass

    def _cursor(self):
        return self.query_one("#disctable", DataTable).cursor_row

    def on_data_table_row_selected(self, event):
        self.app._audition_disc(self.entries[event.cursor_row])

    def action_play(self):
        self.app._audition_disc(self.entries[self._cursor()])

    def action_extract(self):
        self.app._extract_disc([self.entries[self._cursor()]])

    def action_extract_all(self):
        self.app._extract_disc(list(self.entries))

    def action_cancel(self):
        self.app.action_stop_play()
        self.dismiss(None)


class PromptScreen(ModalScreen):
    """A one-line text prompt (output dir, carve range, search pattern...). Enter
    submits, esc cancels. dismiss()es with the typed string, or None."""

    CSS = """
    PromptScreen { align: center middle; }
    #dpbox { width: 76; height: auto; border: round #FF4D00;
             background: #16181C; padding: 1 2; }
    #dphint { color: #8A9099; padding-bottom: 1; }
    """
    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, prompt, default):
        super().__init__()
        self.prompt = prompt
        self.default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="dpbox"):
            yield Static(Text(self.prompt, style=f"bold {ACCENT}"), id="dphint")
            yield Input(value=self.default, id="dpinput")

    def on_mount(self):
        self.query_one(Input).focus()

    def on_input_submitted(self, event):
        self.dismiss(event.value.strip() or None)

    def action_cancel(self):
        self.dismiss(None)




class YesNoScreen(ModalScreen):
    """A plain proceed/cancel prompt. dismiss()es True or False.

    ConfirmScreen answers a three-way save/discard/cancel question, which is the
    wrong shape for "are you sure". Kept separate rather than overloading it,
    so neither prompt has to explain which of its buttons do not apply.
    """

    CSS = """
    YesNoScreen { align: center middle; }
    #ynbox { width: 66; height: auto; border: round #FF4D00;
             background: #16181C; padding: 1 2; }
    #ynmsg { color: #C9CDD3; padding-bottom: 1; }
    #ynbtns { height: auto; }
    """
    # The buttons are a convenience, not the interface: this prompt guards a
    # loudness hazard, so it has to be answerable without a mouse.
    BINDINGS = [
        ("y", "yes", "yes"),
        ("n", "cancel", "no"),
        ("escape", "cancel", "cancel"),
    ]

    def __init__(self, prompt, yes_label="play anyway"):
        super().__init__()
        self.prompt = prompt
        self.yes_label = yes_label

    def compose(self) -> ComposeResult:
        with Vertical(id="ynbox"):
            yield Static(Text(self.prompt, style=f"bold {PEND}"), id="ynmsg")
            with Horizontal(id="ynbtns"):
                yield Button(f"{self.yes_label}  (y)", id="yes", variant="warning")
                yield Button("cancel  (n)", id="cancel")

    def on_mount(self):
        # focus the SAFE choice, so a reflexive enter cancels rather than plays
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "yes")

    def action_yes(self):
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(False)
