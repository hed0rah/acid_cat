"""Generate docs/formats/ableton-anatomy.html from the locked krz shell.

Reuses the head (CSS, favicon, fonts), the byte-map builder and the theme
toggle verbatim, so the aesthetic cannot drift; only the title, the body and
the byte-map SPECS are ours. Every byte below is copied from a real specimen.
"""
import pathlib
import re

SRC = pathlib.Path("docs/formats/krz-anatomy.html")
OUT = pathlib.Path("docs/formats/ableton-anatomy.html")
s = SRC.read_text(encoding="utf-8")

head = s[:s.index("</head>") + len("</head>")]
head = head.replace("<title>acidcat / krz anatomy</title>",
                    "<title>acidcat / ableton anatomy</title>")

builder_start = s.index("<script>", s.index("</head>"))
toggle_start = s.rindex("<script>")
builder = s[builder_start:toggle_start]
toggle = s[toggle_start:]


def brace_span(text, start):
    """End index (exclusive) of the object literal opening at `start`."""
    depth, i, instr, esc = 0, start, None, False
    while i < len(text):
        ch = text[i]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == instr:
                instr = None
        elif ch in "\"'":
            instr = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced")


spec_open = builder.index("var SPECS={") + len("var SPECS=")
spec_end = brace_span(builder, spec_open)

SPECS = """{
    "asd-hdr":["byte",
      [0x06,0x49,0x49,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0xAB,0x00,0x00,0x00,0xAE,0x03,0x00,0x00],
      [
        {label:"marker",k:"sync",r:[0,0],val:"0x06",
          body:"Constant 0x06 in every one of the 8,067 parseable specimens. Byte 0 never varies; it is byte 1 that carries the variation.",
          note:"On its own this is far too weak to identify a file, which is why the sniffer also requires the reserved word at offset 6 to be zero."},
        {label:"byte_order",k:"sync",r:[1,1],val:"0x49 = 'I'",sel:0,
          branch:[["'I' (0x49)","little-endian -- Intel. 7,748 of 8,067 specimens."],["'M' (0x4D)","big-endian -- Motorola. 319 specimens, PowerPC-era Macs."]],
          body:"The TIFF convention: 'I' for Intel, 'M' for Motorola. Every multi-byte field in the file follows it. The big-endian files parse ONLY big-endian -- this is a real byte-order switch, not a version number.",
          note:"Missing this is the difference between a clean parse and 319 files that look corrupt."},
        {label:"count",k:"enum",r:[2,5],val:"329",
          body:"Number of grid entries plus one. The grid that follows holds count-1 positions; this held in all 8,067 specimens.",
          note:"0x00000149 little-endian = 329, so 328 positions follow."},
        {label:"reserved",k:"rsv",r:[6,9],val:"0",
          body:"Zero in every parseable specimen. Because two magic bytes carry so little information, this word is what makes identification safe -- a random binary passing 0x06, a byte-order mark AND a zero word here is vanishingly unlikely.",
          note:"acidcat reports a non-zero value as a warning rather than refusing the file."},
        {label:"grid[0]",k:"flag",r:[10,13],val:"171",
          body:"First frame position. The grid is absolute sample offsets into the source audio, strictly increasing.",
          note:"Not necessarily 0 -- the first entry is the end of the first analysis block."},
        {label:"grid[1]",k:"flag",r:[14,17],val:"942",
          body:"Second position. The step from 171 to 942 is 771 frames = 17.5 ms at 44.1 kHz, comfortably under the 30 ms ceiling.",
          note:"Steps shorten around transients and never exceed the cap."}
      ]
    ],
    "asd-name":["byte",
      [0x0B,0x00,0x00,0x00,0x57,0x00,0x61,0x00,0x72,0x00,0x70,0x00,0x4D,0x00,0x61,0x00,0x72,0x00,0x6B,0x00,0x65,0x00,0x72,0x00,0x73,0x00],
      [
        {label:"char_count",k:"sync",r:[0,3],val:"11",
          body:"Little-endian u32: the length of the name in CHARACTERS, not bytes. The name occupies twice this many bytes.",
          note:"This prefix is what separates a real field name from an accidental run of ASCII-range UTF-16, and it is the whole basis of the name scan."},
        {label:"name (UTF-16LE)",k:"enum",r:[4,25],val:"\\"WarpMarkers\\"",
          body:"The field name, UTF-16 little-endian. Every other byte is 0x00, which is exactly why an ASCII string scan finds nothing here and the format reads as opaque.",
          note:"69 distinct field names were recovered across the specimen set. Together they are a map of everything Live records about the audio."}
      ]
    ]
  }"""

builder = builder[:spec_open] + SPECS + builder[spec_end:]

BODY = r"""<body>
<div class="sheet">

  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>Ableton Anatomy</h1></div><acidcat-toggle aria-label="Toggle light and dark theme"></acidcat-toggle></div>
      <div class="stamp"><b>Live 9 &ndash; Live 12</b>.asd . .als . .adg . .amxd<br>rev 2026.08</div>
    </div>
    <div class="strip">
      <div>magic <b>06 'I' / 06 'M'</b></div><div>endian <b>both, declared</b></div>
      <div>container <b>grid + object tree</b></div><div>corpus <b>8,196 specimens</b></div>
    </div>
  </div>

  <div class="intro">
  <p class="plede lede">Ableton writes a <b>.asd</b> beside every audio file it analyses. It is usually described as a waveform cache, and that is wrong: it holds <b>warp markers, loop points, onsets, pitch marks and time signature</b> &mdash; Live's whole reading of the audio. The head is a <b>frame-position grid</b> whose last entry is the source file's exact frame count and whose steps never exceed <b>30&nbsp;ms</b> of audio; because that ceiling is a fixed fraction of the sample rate, an <b>orphaned sidecar still describes audio that has been deleted</b>. The body is a serialised object tree whose field names are stored in <b>UTF-16</b>, which is why an ASCII scan makes the file look like noise. Everything here was derived from <b>8,196 real specimens</b> and verified frame-for-frame against the source audio on 22 of them. The other tabs cover the rest of the Live footprint. <b>Hover</b> a field to light its bytes.</p>
  <aside class="sig" aria-label="color key">
    <div class="legrows">
    <div class="row"><span class="sw dark k-enum">moss</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
    <div class="row"><span class="sw dark k-flag">ochre</span><span class="swsep">&#8594;</span><span class="sw light k-flag">grid</span></div>
    <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
    <div class="row"><span class="sw dark k-rsv">faint</span><span class="swsep">&#8594;</span><span class="sw light k-rsv">reserved</span></div>
    </div>
    <div class="siglabel">color key</div>
  </aside>
  </div>

  <div class="tabs" id="tabs">
    <button class="tab on" data-p="overview">Overview<small>the family</small></button>
    <button class="tab" data-p="header">.asd header<small>10 bytes</small></button>
    <button class="tab" data-p="grid">Frame grid<small>the 30 ms rule</small></button>
    <button class="tab" data-p="objects">Object tree<small>UTF-16 names</small></button>
    <button class="tab" data-p="liveset">Live Set family<small>.als .alc .adg .adv</small></button>
    <button class="tab" data-p="m4l">Max for Live<small>.amxd</small></button>
    <button class="tab" data-p="impostors">Impostors<small>what lies</small></button>
  </div>
  <div class="panelwrap">

    <!-- ===================== OVERVIEW ===================== -->
    <div class="panel on" data-panel="overview">
      <div class="pfacts"><span>binary<b>.asd .amxd</b></span><span>gzipped XML<b>.als .alc .adg .adv</b></span><span>archive<b>.alp</b></span><span>specimens<b>8,635</b></span></div>
      <p class="plede">Three unrelated shapes ship under one brand. Only <b>.asd</b> needed reverse engineering; the Live Set family is gzipped XML that says what it is, and <b>.amxd</b> is a plain chunk chain. Grouping them here rather than across five pages reflects how they actually sit in a library &mdash; a project folder holds all of them at once.</p>
      <div class="sec">the Ableton footprint in a real library</div>
      <table class="tbl">
        <thead><tr><th>ext</th><th>what it is</th><th>shape</th><th>seen</th><th>walked</th></tr></thead>
        <tbody>
        <tr><td><code>.asd</code></td><td>per-sample analysis sidecar</td><td>binary, byte-order-declared</td><td>8,196</td><td>yes</td></tr>
        <tr><td><code>.adg</code></td><td>device group / rack preset</td><td>gzip + XML <code>&lt;GroupDevicePreset&gt;</code></td><td>217</td><td>yes</td></tr>
        <tr><td><code>.als</code></td><td>Live Set (project)</td><td>gzip + XML <code>&lt;LiveSet&gt;</code></td><td>175</td><td>yes</td></tr>
        <tr><td><code>.alp</code></td><td>Live Pack</td><td>gzip archive, not XML</td><td>22</td><td>as a container</td></tr>
        <tr><td><code>.alc</code></td><td>Live Clip</td><td>gzip + XML <code>&lt;LiveSet&gt;</code></td><td>12</td><td>yes</td></tr>
        <tr><td><code>.adv</code></td><td>device preset</td><td>gzip + XML, device-named root</td><td>9</td><td>yes</td></tr>
        <tr><td><code>.amxd</code></td><td>Max for Live device</td><td><code>ampf</code> chunk chain</td><td>4</td><td>yes</td></tr>
        </tbody>
      </table>
      <div class="callout"><b>The sidecar outnumbers everything else 37 to 1.</b> In a producer's library the Ableton footprint is overwhelmingly <code>.asd</code> &mdash; one per analysed sample. Any tool that reports them as unknown is calling a large fraction of the collection noise.</div>
    </div>

    <!-- ===================== HEADER ===================== -->
    <div class="panel" data-panel="header">
      <div class="pfacts"><span>size<b>10 bytes</b></span><span>magic<b>0x06 + BOM</b></span><span>count<b>grid + 1</b></span><span>reserved<b>always 0</b></span></div>
      <p class="plede">A ten-byte head, then the grid. The interesting part is byte 1: it is a <b>byte-order mark</b> in the TIFF style, <code>'I'</code> for Intel and <code>'M'</code> for Motorola. 319 of the 8,067 parseable specimens are big-endian &mdash; PowerPC-era Mac files &mdash; and they parse only big-endian. A reader that assumes little-endian silently mangles them.</p>
      <div class="sec">.asd header + first grid entries (18 bytes)</div>
      <p class="note">Example: <code>DO Crash B1.wav.asd</code> &mdash; 44.1 kHz stereo, 7.774 s, Live 12.</p>
      <div id="asd-hdr" data-build></div>
      <div class="callout"><b>Two magic bytes are not an identification.</b> 0x06 plus a byte-order mark would fire on plenty of unrelated binaries, so acidcat also requires the reserved word at offset 6 to be zero and the entry count to be sane. Measured on 4,994 real non-Ableton files: zero false positives.</div>
    </div>

    <!-- ===================== GRID ===================== -->
    <div class="panel" data-panel="grid">
      <div class="pfacts"><span>entries<b>count - 1</b></span><span>type<b>u32 frame offsets</b></span><span>ceiling<b>30 ms of audio</b></span><span>last entry<b>= total frames</b></span></div>
      <p class="plede">The grid is a strictly increasing list of <b>absolute sample offsets</b> into the source audio. Two properties make it far more useful than it looks. First, the <b>last entry equals the source file's exact total frame count</b> &mdash; verified against the audio on 22 specimens, 22 matches, no rounding. Second, <b>no step ever exceeds 30&nbsp;ms of audio</b>: 1,323 frames at 44.1&nbsp;kHz, 1,440 at 48&nbsp;kHz, and so on.</p>
      <div class="sec">recovering a deleted file from its sidecar</div>
      <p class="note">Because the ceiling is a fixed fraction of the rate, the largest observed step identifies the rate, and the last entry then gives the duration.</p>
      <table class="tbl">
        <thead><tr><th>quantity</th><th>how</th><th>confidence</th></tr></thead>
        <tbody>
        <tr><td>sample rate</td><td>smallest standard rate whose 30 ms grain is not smaller than the largest step</td><td>exact when the ceiling was reached, otherwise a lower bound</td></tr>
        <tr><td>frame count</td><td>last grid entry, read directly</td><td>exact</td></tr>
        <tr><td>duration</td><td>frame count / rate</td><td>inherits the rate's</td></tr>
        </tbody>
      </table>
      <div class="callout"><b>This is the reason to walk the format.</b> An orphaned <code>.asd</code> in a folder is evidence about a file that no longer exists &mdash; its rate, its length, its exact sample count. Nothing else in the toolchain recovers that, because nothing else survives the audio.</div>
      <div class="callout"><b>What is NOT here: tempo.</b> Live stores warp markers as (sample position, beat time) pairs and derives tempo from them, so an unwarped sample records no tempo at all. Checked against 599 files whose own filenames state their BPM: the number is absent, not hidden. acidcat therefore does not report a tempo from a <code>.asd</code>, rather than reporting a guess.</div>
    </div>

    <!-- ===================== OBJECTS ===================== -->
    <div class="panel" data-panel="objects">
      <div class="pfacts"><span>names<b>u32 + UTF-16LE</b></span><span>distinct<b>69</b></span><span>layout<b>interleaved</b></span><span>version<b>per object</b></span></div>
      <p class="plede">After the grid comes a serialised object tree that <b>writes its own field names inline</b>. They are length-prefixed <b>UTF-16</b>, so every second byte is zero and an ASCII string scan returns nothing &mdash; which is most of why this format has a reputation for being opaque. The names are not gathered in a header; they are <b>interleaved with their data</b> across the whole file, so a parser can seek a field by name instead of by a version-specific offset.</p>
      <div class="sec">a field name on disk (26 bytes)</div>
      <p class="note">The same specimen. Note the 0x00 after every character.</p>
      <div id="asd-name" data-build></div>
      <div class="sec">what the names reveal</div>
      <table class="tbl">
        <thead><tr><th>group</th><th>fields</th></tr></thead>
        <tbody>
        <tr><td>warp</td><td><code>WarpMarkers</code> <code>IsWarped</code> <code>WarpMode</code> <code>TransientResolution</code></td></tr>
        <tr><td>clip / loop</td><td><code>LoopStart</code> <code>LoopEnd</code> <code>HiddenLoopStart</code> <code>SampleOffset</code></td></tr>
        <tr><td>metre</td><td><code>Numerator</code> <code>Denominator</code> <code>AufTaktData</code> (anacrusis)</td></tr>
        <tr><td>onsets</td><td><code>OnSets</code> <code>UserOnsets</code> <code>HasUserOnsets</code> <code>PitchMarks</code></td></tr>
        <tr><td>staleness</td><td><code>OriginalFileSize</code></td></tr>
        </tbody>
      </table>
      <div class="callout"><b>Two generations, no version byte.</b> The magic is identical across both, and versioning is carried <i>per object</i> as individual <code>Version</code> ints. The older generation declares <code>InitialBPM</code>, <code>Bpms</code>, <code>BeatTrackState</code> and <code>Tonalities</code>; the newer one drops those for <code>UnbiasedTempoEstimate</code> and an <code>OverViewLevels</code> pyramid. The field set is the only reliable tell.</div>
      <div class="callout"><b>OriginalFileSize is the staleness check.</b> Live records the source audio's byte size and re-analyses on mismatch &mdash; the same mechanism REAPER uses (mtime + size) arrived at independently. It detects an edit, but cannot re-find the file after a rename: the sidecar is bound to its audio by filename alone, so moving the audio orphans it silently.</div>
    </div>

    <!-- ===================== LIVE SET FAMILY ===================== -->
    <div class="panel" data-panel="liveset">
      <div class="pfacts"><span>container<b>gzip</b></span><span>payload<b>UTF-8 XML</b></span><span>expansion<b>~20x</b></span><span>root<b>&lt;Ableton&gt;</b></span></div>
      <p class="plede">Sets, clips, racks and device presets are all the same thing: a gzip stream wrapping one XML document whose root is <code>&lt;Ableton&gt;</code>. The root carries the Live build that wrote it, which makes these the easiest provenance evidence in the whole ecosystem &mdash; an exact version string, not an inference.</p>
      <div class="sec">the root element</div>
      <pre class="code">&lt;Ableton MajorVersion="5" MinorVersion="12.0_12120"
         SchemaChangeCount="2" Creator="Ableton Live 12.1.5"
         Revision="18e155016678da1939bdc3938f981adf84ebc96d"&gt;</pre>
      <div class="sec">telling them apart</div>
      <table class="tbl">
        <thead><tr><th>ext</th><th>root's first child</th><th>note</th></tr></thead>
        <tbody>
        <tr><td><code>.als</code></td><td><code>&lt;LiveSet&gt;</code></td><td>a project</td></tr>
        <tr><td><code>.alc</code></td><td><code>&lt;LiveSet&gt;</code></td><td>identical to a Set in content</td></tr>
        <tr><td><code>.adg</code></td><td><code>&lt;GroupDevicePreset&gt;</code></td><td>rack</td></tr>
        <tr><td><code>.adv</code></td><td>the device's own class</td><td><code>&lt;Operator&gt;</code>, <code>&lt;Wavetable&gt;</code>, ...</td></tr>
        </tbody>
      </table>
      <div class="callout"><b>A Set and a Clip are indistinguishable by content.</b> Both open <code>&lt;LiveSet&gt;</code>. Only the extension separates them, so that is what acidcat uses &mdash; and says so, rather than inventing a content rule that would be wrong half the time.</div>
      <div class="callout"><b>Capped decompression.</b> A 336 KB Set expanded to 6.9 MB of XML in testing, about 20x. acidcat caps every Ableton decompression at 64 MB and reports when a file hits the ceiling, so a crafted or merely enormous document cannot force an unbounded allocation.</div>
    </div>

    <!-- ===================== MAX FOR LIVE ===================== -->
    <div class="panel" data-panel="m4l">
      <div class="pfacts"><span>magic<b>"ampf"</b></span><span>header<b>12 bytes</b></span><span>chain<b>id + u32 length</b></span><span>payload<b>Max patcher JSON</b></span></div>
      <p class="plede">A Max for Live device is an <b>Ableton Max Patch Format</b> container: the ASCII magic <code>ampf</code>, a version word, and then &mdash; before the chunk chain begins &mdash; a <b>four-byte marker</b>, <code>aaaa</code> in every specimen seen. Only at offset 12 does the chain start: four-byte id, little-endian length, payload. The <code>ptch</code> chunk carries the Max patcher, a short binary preamble followed by JSON.</p>
      <div class="sec">layout</div>
      <pre class="code">0x00  "ampf"  u32:version  "aaaa"      &lt;- 12-byte header
0x0C  "meta"  u32:len      ...
0x18  "ptch"  u32:len      mx@c ... { patcher JSON }</pre>
      <div class="callout"><b>The marker is not a chunk, and mistaking it for one is loud.</b> Reading <code>aaaa</code> as an id makes the following four bytes &mdash; the ASCII <code>meta</code> &mdash; decode as a length of 1,635,018,093 bytes. The first version of this walker did exactly that and reported a single chunk plus an overflow warning on every real device. The chain starts at <b>12</b>, not 8.</div>
      <div class="callout"><b>The length field is not trusted.</b> A chunk claiming more bytes than remain is reported as a warning and the walk stops there rather than reading past the end. In every specimen the <code>ptch</code> chunk ends exactly on the last byte of the file, so a chain that stops short is also worth saying out loud &mdash; and acidcat does.</div>
    </div>

    <!-- ===================== IMPOSTORS ===================== -->
    <div class="panel" data-panel="impostors">
      <div class="pfacts"><span>AppleDouble<b>129 of 8,196</b></span><span>.alp<b>gzip, not XML</b></span></div>
      <p class="plede">Two things in a real library wear Ableton clothing and are not Ableton documents. Both matter because the honest answer is more useful than a forced one.</p>
      <div class="sec">macOS AppleDouble stubs</div>
      <p class="plede"><b>129 of the 8,196 files carrying a <code>.asd</code> extension were not sidecars at all</b> &mdash; they were <code>._name.wav.asd</code> resource-fork stubs, left behind when a Mac writes to a non-HFS volume. They open with <code>00 05 16 07</code> and the string <code>Mac OS X</code>.</p>
      <div class="callout"><b>Named, not swallowed.</b> These stay out of the Ableton format namespace entirely, so <code>classify</code> keeps calling them foreign files &mdash; which is what they are. The walker recognises them only as a courtesy, if one is forced through it.</div>
      <div class="sec">Live Packs</div>
      <p class="plede">A <code>.alp</code> is gzip like the Set family, but it is an <b>archive</b>, not a document: there is no <code>&lt;Ableton&gt;</code> element inside. Rather than mislabel it as a Set, acidcat declines it and lets it report as a generic compressed container whose contents may be audio.</p>
      <div class="callout"><b>Declining is a result.</b> Every gzip file that reaches the Ableton sniffer is offered the chance to be a Live document; the ones that are not say so, and the sniffer returns nothing rather than guessing from the extension.</div>
    </div>

  </div>
</div>
"""

page = head + "\n" + BODY + builder + toggle

# the shell carries krz-specific comments; retitle them so the generated page
# does not claim its bytes came from a Kurzweil bank
page = page.replace(
    "/* krz palette: moss value, ochre flag, slate structural */",
    "/* shared anatomy palette: moss value, ochre grid, slate structural */")
page = page.replace(
    "// every byte value below is copied verbatim from a real Sweetwater specimen\n"
    "  // (e3_bass.krz for the bank objects, verbhall.krz for SROM).",
    "// every byte value below is copied verbatim from a real specimen:\n"
    "  // DO Crash B1.wav.asd, 44.1 kHz stereo, 7.774 s, written by Live 12.")

stale = [w for w in ("Kurzweil", "krz", "PRAM", "SROM", "e3_bass", "Sweetwater",
                     "K2000", "VAST") if w in page]
if stale:
    raise SystemExit(f"template leftovers still in the page: {stale}")

OUT.write_text(page, encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
