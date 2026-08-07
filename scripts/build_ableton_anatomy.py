"""Generate docs/formats/ableton-anatomy.html from the locked krz shell.

Reuses the head (CSS, favicon, fonts), the byte-map builder and the theme
toggle verbatim, so the aesthetic cannot drift; only the title, the body and
the byte-map SPECS are ours. Every byte below is copied from a real specimen.
"""
import pathlib
import re

# The shell is taken from an existing page so the CSS, favicon, theme toggle
# and byte-map builder cannot drift. Pass a source and destination to build
# against the PUBLISHED site instead of the repo copy -- the two have
# diverged before, with the site carrying theme modes the repo lacks, and
# generating from the older shell would ship a visibly inconsistent page.
#     python scripts/build_ableton_anatomy.py <src-page> <out-page>
import sys
SRC = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                   else "docs/formats/krz-anatomy.html")
OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2
                   else "docs/formats/ableton-anatomy.html")
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
          body:"Constant 0x06. Byte 0 never varies; it is byte 1 that carries the variation.",
          note:"On its own this is far too weak to identify a file: a safe check also requires the reserved word at offset 6 to be zero."},
        {label:"byte_order",k:"sync",r:[1,1],val:"0x49 = 'I'",sel:0,
          branch:[["'I' (0x49)","little-endian -- Intel. The common case."],["'M' (0x4D)","big-endian -- Motorola, written by PowerPC-era Macs."]],
          body:"The TIFF convention: 'I' for Intel, 'M' for Motorola. Every multi-byte field in the file follows it. The big-endian files parse ONLY big-endian -- this is a real byte-order switch, not a version number.",
          note:"A reader that ignores it decodes every big-endian file as corrupt."},
        {label:"count",k:"enum",r:[2,5],val:"329",
          body:"Number of grid entries plus one. The grid that follows holds count-1 positions.",
          note:"0x00000149 little-endian = 329, so 328 positions follow."},
        {label:"reserved",k:"rsv",r:[6,9],val:"0",
          body:"Zero in every known file. Because two magic bytes carry so little information, this word is what makes identification safe -- a random binary passing 0x06, a byte-order mark AND a zero word here is vanishingly unlikely.",
          note:"A non-zero value is a sign the file is not what it claims, not proof of it."},
        {label:"grid[0]",k:"flag",r:[10,13],val:"171",
          body:"First frame position. The grid is absolute sample offsets into the source audio, strictly increasing.",
          note:"Not necessarily 0 -- the first entry is the end of the first analysis block."},
        {label:"grid[1]",k:"flag",r:[14,17],val:"942",
          body:"Second position. The step from 171 to 942 is 771 frames = 17.5 ms at 44.1 kHz, comfortably under the 30 ms ceiling.",
          note:"Steps shorten around transients and never exceed the cap."}
      ]
    ],
    "asd-warp":["byte",
      [0x0A,0x57,0x61,0x72,0x70,0x4D,0x61,0x72,0x6B,0x65,0x72,0x01,0x00,0x00,0x00,
       0x97,0x96,0x96,0x96,0x96,0x96,0x86,0x3F,0x00,0x00,0x00,0x00,0x00,0x00,0xA0,0x3F],
      [
        {label:"name length",k:"sync",r:[0,0],val:"10",
          body:"A u8 length, then the class name in ASCII -- the same convention the type dictionary uses. It is what makes a marker self-locating: no count to find and no offset to derive.",
          note:"the class DECLARATION carries this literal too, and the bytes after it are denormals. Requiring sane times and a run starting at id 0 separates them."},
        {label:"class name",k:"sync",r:[1,10],val:"WarpMarker",
          body:"The class name, inline. Live's serialiser writes it before every instance."},
        {label:"Id",k:"enum",r:[11,14],val:"1",
          body:"u32. Matches the Id attribute the Live Set shows for this marker. Markers form a run starting at 0.",
          note:"0x00000001 little-endian."},
        {label:"SecTime",k:"flag",r:[15,22],val:"0.011029411764705883",
          body:"f64: WHERE in the recording, in seconds.",
          note:"a 17-significant-digit double, stored exactly."},
        {label:"BeatTime",k:"flag",r:[23,30],val:"0.03125",
          body:"f64: WHAT MUSICAL POSITION that instant sits at, in beats. The pair is the whole idea -- Live stores a mapping from time to musical position, and everything else follows from it.",
          note:"0.03125 beats in 0.011029... s is 170 BPM, which is exactly the tempo the Live Set declares."}
      ]
    ],
    "asd-onsets":["byte",
      [0x0D,0x00,0x00,0x00,0xD3,0x05,0x00,0x00,0xA2,0x1F,0x00,0x00,0xF8,0x3B,0x00,0x00],
      [
        {label:"count",k:"sync",r:[0,3],val:"13",
          body:"u32: how many transients Live detected. The same count is repeated after the positions, before the energies -- which is what identifies the structure.",
          note:"a count of 1 is refused: with a single position, strictly-increasing constrains nothing."},
        {label:"positions[0]",k:"enum",r:[4,7],val:"1,491",
          body:"u32 frame offset of the first transient. 1,491 frames is 0.031 s at 48 kHz.",
          note:"bounded by the frame count the grid declares."},
        {label:"positions[1]",k:"enum",r:[8,11],val:"8,098"},
        {label:"positions[2]",k:"enum",r:[12,15],val:"15,352",
          body:"Roughly 7,000 frames apart, about 0.15 s -- the pulse of a loop. A one-shot carries a single transient within a few frames of zero."}
      ]
    ],
    "asd-clip":["byte",
      [0x06,0x00,0x00,0x00,0x00,0x00,0xF0,0x41,0x00,0x00,0x82,0x42,0x00,0x00,0xC8,0x41,
       0x02,0x00,0x00,0x00,0x00,0x00,0xC8,0x42,0x00,0x00,0xC8,0x42,0x00,0x00,0x00,0x43],
      [
        {label:"TransientResolution",k:"enum",r:[0,3],val:"6",
          body:"u32. An INTEGER among floats, which is why searching for eight consecutive float32 finds nothing here."},
        {label:"GranularityTones",k:"flag",r:[4,7],val:"30"},
        {label:"GranularityTexture",k:"flag",r:[8,11],val:"65"},
        {label:"FluctuationTexture",k:"flag",r:[12,15],val:"25",
          body:"f32 warp-engine parameters, in the same order the Live Set XML declares them."},
        {label:"TransientLoopMode",k:"enum",r:[16,19],val:"2",
          body:"u32, the second integer in the block."},
        {label:"TransientEnvelope",k:"flag",r:[20,23],val:"100"},
        {label:"ComplexProFormants",k:"flag",r:[24,27],val:"100"},
        {label:"ComplexProEnvelope",k:"flag",r:[28,31],val:"128",
          body:"f32, default 128. This value appears five times per file and was twice mistaken for something else here -- first for the project tempo, then written off as a meaningless constant. It is a warp-engine parameter with a default.",
          note:"the Live Set XML names it, which is how it was finally identified."}
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
          note:"69 distinct field names are known. Together they map everything Live records about the audio."}
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
      <div>container <b>grid + object tree</b></div><div>grain <b>30 ms ceiling</b></div>
    </div>
  </div>

  <div class="intro">
  <p class="plede lede">Ableton writes a <b>.asd</b> beside every audio file it analyses. It is usually described as a waveform cache, and that is wrong: it holds <b>warp markers, loop points, onsets, pitch marks and time signature</b> &mdash; Live's whole reading of the audio. The head is a <b>frame-position grid</b> whose last entry is the source file's exact frame count and whose steps never exceed <b>30&nbsp;ms</b> of audio; because that ceiling is a fixed fraction of the sample rate, an <b>orphaned sidecar still describes audio that has been deleted</b>. The body is a serialised object tree whose field names are stored in <b>UTF-16</b>, which is why an ASCII scan makes the file look like noise. It also states a <b>tempo</b> -- not as a number, but as a mapping from seconds to beats that the tempo falls out of. <b>Hover</b> a field to light its bytes.</p>
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
    <button class="tab" data-p="warp">Warp + tempo<small>the mapping</small></button>
    <button class="tab" data-p="onsets">Onsets<small>transients</small></button>
    <button class="tab" data-p="liveset">Live Set family<small>.als .alc .adg .adv</small></button>
    <button class="tab" data-p="m4l">Max for Live<small>.amxd</small></button>
    <button class="tab" data-p="impostors">Impostors<small>what lies</small></button>
  </div>
  <div class="panelwrap">

    <!-- ===================== OVERVIEW ===================== -->
    <div class="panel on" data-panel="overview">
      <div class="pfacts"><span>binary<b>.asd .amxd</b></span><span>gzipped XML<b>.als .alc .adg .adv</b></span><span>archive<b>.alp</b></span><span>chunked<b>.amxd</b></span></div>
      <p class="plede">Three unrelated shapes ship under one brand. <b>.asd</b> is binary and undocumented; the Live Set family is gzipped XML that names its own type; <b>.amxd</b> is a plain chunk chain. They are grouped here because a project folder holds all of them at once.</p>
      <div class="sec">the document types</div>
      <table class="tbl">
        <thead><tr><th>ext</th><th>what it is</th><th>shape</th></tr></thead>
        <tbody>
        <tr><td><code>.asd</code></td><td>per-sample analysis sidecar</td><td>binary, byte-order-declared</td></tr>
        <tr><td><code>.adg</code></td><td>device group / rack preset</td><td>gzip + XML <code>&lt;GroupDevicePreset&gt;</code></td></tr>
        <tr><td><code>.als</code></td><td>Live Set (project)</td><td>gzip + XML <code>&lt;LiveSet&gt;</code></td></tr>
        <tr><td><code>.alp</code></td><td>Live Pack</td><td>gzip archive, not XML</td></tr>
        <tr><td><code>.alc</code></td><td>Live Clip</td><td>gzip + XML <code>&lt;LiveSet&gt;</code></td></tr>
        <tr><td><code>.adv</code></td><td>device preset</td><td>gzip + XML, device-named root</td></tr>
        <tr><td><code>.amxd</code></td><td>Max for Live device</td><td><code>ampf</code> chunk chain</td></tr>
        <tr><td><code>.agr</code></td><td>groove / quantize</td><td>gzip + XML <code>&lt;Groove&gt;</code></td></tr>
        </tbody>
      </table>
      <div class="callout"><b>Live has more document types than these.</b> <code>.ams</code> (Operator meta sound), <code>.abl</code> and <code>.ablbundle</code> (Note) and <code>.ask</code> (theme) are the same gzip + <code>&lt;Ableton&gt;</code> XML shape. The root child element names the type, so an unmapped document still identifies itself.</div>
    </div>

    <!-- ===================== HEADER ===================== -->
    <div class="panel" data-panel="header">
      <div class="pfacts"><span>size<b>10 bytes</b></span><span>magic<b>0x06 + BOM</b></span><span>count<b>grid + 1</b></span><span>reserved<b>always 0</b></span></div>
      <p class="plede">A ten-byte head, then the grid. The interesting part is byte 1: it is a <b>byte-order mark</b> in the TIFF style, <code>'I'</code> for Intel and <code>'M'</code> for Motorola. Big-endian files, written by PowerPC-era Macs, exist and parse only big-endian. A reader that assumes little-endian silently mangles them.</p>
      <div class="sec">.asd header + first grid entries (18 bytes)</div>
      <p class="note">A 44.1 kHz stereo sidecar, 7.774 s of source audio, written by Live 12.</p>
      <div id="asd-hdr" data-build></div>
      <div class="callout"><b>Two magic bytes are not an identification.</b> 0x06 plus a byte-order mark would fire on plenty of unrelated binaries. A safe check also requires the reserved word at offset 6 to be zero and the entry count to be sane.</div>
    </div>

    <!-- ===================== GRID ===================== -->
    <div class="panel" data-panel="grid">
      <div class="pfacts"><span>entries<b>count - 1</b></span><span>type<b>u32 frame offsets</b></span><span>ceiling<b>30 ms of audio</b></span><span>last entry<b>= total frames</b></span></div>
      <p class="plede">The grid is a strictly increasing list of <b>absolute sample offsets</b> into the source audio. Two properties make it far more useful than it looks. First, the <b>last entry equals the source file's exact total frame count</b>, with no rounding. Second, <b>no step ever exceeds 30&nbsp;ms of audio</b>: 1,323 frames at 44.1&nbsp;kHz, 1,440 at 48&nbsp;kHz, and so on.</p>
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
      <div class="callout"><b>No tempo NUMBER is stored, but the tempo is recoverable.</b> The value appears nowhere &mdash; at no byte offset, as neither f32 nor f64, and in no derived form. Live keeps a mapping instead: warp markers pin seconds to beats, and the tempo falls out of any two of them. See the Warp tab.</div>
    </div>

    <!-- ===================== OBJECTS ===================== -->
    <div class="panel" data-panel="objects">
      <div class="pfacts"><span>names<b>u32 + UTF-16LE</b></span><span>distinct<b>69</b></span><span>layout<b>interleaved</b></span><span>version<b>per object</b></span></div>
      <p class="plede">After the grid comes a serialised object tree that <b>writes its own field names inline</b>. They are length-prefixed <b>UTF-16</b>, so every second byte is zero and an ASCII string scan returns nothing &mdash; which is most of why this format has a reputation for being opaque. The names are not gathered in a header; they are <b>interleaved with their data</b> across the whole file, so a parser can seek a field by name instead of by a version-specific offset.</p>
      <div class="sec">a field name on disk (26 bytes)</div>
      <p class="note">Note the 0x00 after every character.</p>
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
      <div class="callout"><b>Optional sections, not generations.</b> It is tempting to read the field set as a format version. It is not one: over 1,473 sidecars <b>62% declare BOTH</b> the beat-tracking set (<code>InitialBPM</code>, <code>Bpms</code>, <code>BeatTrackState</code>, <code>Tonalities</code>) and the overview set (<code>OverViewLevels</code>, <code>UnbiasedTempoEstimate</code>), so they cannot be generations of one another. Versioning is carried <i>per object</i> as individual <code>Version</code> ints, and there is no global version byte.</div>
      <div class="callout"><b>OriginalFileSize is the staleness check.</b> Live records the source audio's byte size and re-analyses on mismatch &mdash; the same mechanism REAPER uses (mtime + size), arrived at independently. Of 1,200 sidecars sitting beside their real audio, <b>96% reference its current size</b>; one that does not is describing a version of the file that no longer exists. It detects an edit but cannot re-find the file after a rename: the binding is by filename alone, so moving the audio orphans it silently.</div>
      <div class="callout"><b>Declared is not stored.</b> This schema is shared with the Live Set, so it names things the sidecar never carries. <code>LoopStart</code>, <code>LoopEnd</code>, <code>IsWarped</code>, <code>WarpMode</code>, <code>Numerator</code> and <code>Denominator</code> are all declared here &mdash; and <code>LoopEnd</code> is <b>absent from 91%</b> of files whose Set states a real one. Loop points belong to a CLIP, and one audio file can back many clips with different loops. What the sidecar holds is per-file ANALYSIS.</div>
    </div>

    <!-- ===================== WARP + TEMPO ===================== -->
    <div class="panel" data-panel="warp">
      <div class="pfacts"><span>record<b>31 bytes</b></span><span>SecTime<b>f64 seconds</b></span><span>BeatTime<b>f64 beats</b></span><span>tempo<b>derived</b></span></div>
      <p class="plede">A warp marker pins one instant of the recording to one position in the bar. Each record carries its own class name inline, u8-length-prefixed, so the markers are <b>self-locating</b> &mdash; there is no count to find and no offset to derive.</p>
      <div class="sec">one warp marker (31 bytes)</div>
      <p class="note">Marker id 1 from a real project sidecar. The Live Set that wrote it states exactly these values.</p>
      <div id="asd-warp" data-build></div>
      <div class="callout"><b>The tempo is the mapping, not a number.</b> Nothing in a <code>.asd</code> stores a BPM, at any byte offset, as neither f32 nor f64, and in no derived form. But two markers give it directly: <code>(beats / seconds) x 60</code>. Here 0.03125 beats in 0.011029411764705883 s is <b>170 BPM</b>, and the Live Set declares 170. Across 40 clips the derivation matched the declared tempo every time, and over a 2,000-file sample it yields only clean musical values: 140, 170, 138, 120, 150, 145, 70, 172.</div>
      <div class="callout"><b>Most files have none, and that is not a gap.</b> A one-shot nobody warped has nothing to pin, so most sidecars carry no markers at all. A clip the Live Set marks unwarped never has them. The presence of this section is not a format version: it appears under Live 9.7 through 12, and one project written by a single version contains sidecars both with and without it &mdash; a <code>.asd</code> is written when the audio is analysed, and outlives the projects that use it.</div>
    </div>

    <!-- ===================== ONSETS ===================== -->
    <div class="panel" data-panel="onsets">
      <div class="pfacts"><span>shape<b>two arrays</b></span><span>positions<b>u32 frames</b></span><span>energies<b>f32</b></span><span>count<b>repeated</b></span></div>
      <p class="plede">Live's transient detection, stored as two length-prefixed arrays back to back: the count, the frame positions, the count <i>again</i>, then one energy per onset. That repeated count is what identifies the structure in a file with no other landmark.</p>
      <div class="sec">onset array head (16 bytes)</div>
      <p class="note">A 13-transient loop. Positions are absolute frame offsets, checkable against the frame count the grid already gave us.</p>
      <div id="asd-onsets" data-build></div>
      <div class="sec">warp-engine parameters (32 bytes)</div>
      <p class="note">Sitting just above the onsets, in the order the Live Set declares them.</p>
      <div id="asd-clip" data-build></div>
      <div class="callout"><b>Two traps, both paid for.</b> The arrays are <b>not</b> word-aligned to the end of the frame grid, so a 4-stepped scan walks straight past them. And a count of 1 must be refused: with a single position "strictly increasing" constrains nothing, so any stray pair of equal u32 satisfies the test &mdash; accepting it gave 7 of 14 files a confidently wrong answer. A genuine one-shot is reported as unknown rather than guessed at.</div>
      <div class="callout"><b>Onsets are not warp markers.</b> Live snaps markers to detected transients, so a marker frequently lands exactly on an onset &mdash; 12 of 57 across five files. The counts are what separate them: one file carries 14 onsets and 9 markers. Reading one as the other looks convincing and is wrong.</div>
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
      <div class="callout"><b>The expansion ratio is the hazard.</b> A 336 KB Set becomes about 6.9 MB of XML, roughly 20x. The compressed size therefore says nothing about how much memory the document needs, so a reader must impose its own ceiling and say when a file reaches it.</div>
    </div>

    <!-- ===================== MAX FOR LIVE ===================== -->
    <div class="panel" data-panel="m4l">
      <div class="pfacts"><span>magic<b>"ampf"</b></span><span>header<b>12 bytes</b></span><span>chain<b>id + u32 length</b></span><span>payload<b>Max patcher JSON</b></span></div>
      <p class="plede">A Max for Live device is an <b>Ableton Max Patch Format</b> container: the ASCII magic <code>ampf</code>, a version word, and then &mdash; before the chunk chain begins &mdash; a <b>four-byte marker</b>, <code>aaaa</code>. Only at offset 12 does the chain start: four-byte id, little-endian length, payload. The <code>ptch</code> chunk carries the Max patcher, a short binary preamble followed by JSON.</p>
      <div class="sec">layout</div>
      <pre class="code">0x00  "ampf"  u32:version  "aaaa"      &lt;- 12-byte header
0x0C  "meta"  u32:len      ...
0x18  "ptch"  u32:len      mx@c ... { patcher JSON }</pre>
      <div class="callout"><b>The marker is not a chunk, and mistaking it for one is loud.</b> Reading <code>aaaa</code> as an id makes the following four bytes &mdash; the ASCII <code>meta</code> &mdash; decode as a length of 1,635,018,093 bytes, and the whole chain collapses to one impossible chunk. The chain starts at <b>12</b>, not 8.</div>
      <div class="callout"><b>The length field cannot be trusted.</b> A chunk claiming more bytes than remain must stop the walk rather than read past the end. The <code>ptch</code> chunk ends exactly on the last byte of the file, so a chain that stops short means something is missing.</div>
    </div>

    <!-- ===================== IMPOSTORS ===================== -->
    <div class="panel" data-panel="impostors">
      <div class="pfacts"><span>AppleDouble<b>._name stubs</b></span><span>.alp<b>gzip, not XML</b></span></div>
      <p class="plede">Two things in a real library wear Ableton clothing and are not Ableton documents. Both matter because the honest answer is more useful than a forced one.</p>
      <div class="sec">macOS AppleDouble stubs</div>
      <p class="plede"><b>Not every file with a <code>.asd</code> extension is a sidecar.</b> <code>._name.wav.asd</code> resource-fork stubs are left behind when a Mac writes to a non-HFS volume, and they are common enough to matter. They open with <code>00 05 16 07</code> and the string <code>Mac OS X</code>.</p>
      <div class="callout"><b>Named, not swallowed.</b> They belong to AppleDouble, not to Ableton, and identifying them as such is what keeps them from being counted as damaged sidecars.</div>
      <div class="sec">Live Packs</div>
      <p class="plede">A <code>.alp</code> is gzip like the Set family, but it is an <b>archive</b>, not a document: there is no <code>&lt;Ableton&gt;</code> element inside. The gzip magic alone therefore cannot tell a Pack from a Set; only the root element can.</p>
      <div class="callout"><b>Declining is a result.</b> A gzip file either contains an <code>&lt;Ableton&gt;</code> root or it does not. The extension is not evidence either way.</div>
    </div>

  </div>
</div>
"""

# The shell styles the components it was built for, and the body may not use
# anything else. `.tbl` was used four times with no rule anywhere, so those
# tables rendered as raw unspaced text with one column running into the next --
# and every validator passed, because the markup is perfectly well formed. Add
# the rule to the head, in the shell's own idiom.
TBL_CSS = (
    ".tbl{border-collapse:collapse;font-size:0.7rem;width:100%;margin:0.5rem 0 1.3rem;"
    "display:block;overflow-x:auto;max-width:100%}"
    ".tbl th,.tbl td{text-align:left;padding:0.34rem 1.1rem 0.34rem 0;"
    "border-bottom:1px solid var(--hair);vertical-align:top}"
    ".tbl th{font-size:0.56rem;letter-spacing:0.14em;text-transform:uppercase;"
    "color:var(--faint);border-bottom:1px solid var(--line);white-space:nowrap}"
    ".tbl td:first-child{color:var(--ink);white-space:nowrap}"
)
head = head.replace("</style>", TBL_CSS + "</style>", 1)

page = head + "\n" + BODY + builder + toggle

# A class with no rule is invisible to every check we have: the HTML is valid,
# the byte maps validate, the page renders -- it just looks wrong. So make it a
# build error instead of something a human has to notice.
_body_html = page[page.index("<body"):page.index("<script", page.index("<body"))]
_css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", page, re.S))
_defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", _css))
_used = set()
for _m in re.finditer(r'class="([^"]+)"', _body_html):
    _used.update(_m.group(1).split())
# `code` and `tt` are element/typography selectors carried by the shell
_orphans = sorted(_used - _defined - {"code", "tt"})
if _orphans:
    raise SystemExit(f"body uses classes the shell does not style: {_orphans}")

# the shell carries krz-specific comments; retitle them so the generated page
# does not claim its bytes came from a Kurzweil bank
page = page.replace(
    "/* krz palette: moss value, ochre flag, slate structural */",
    "/* shared anatomy palette: moss value, ochre grid, slate structural */")
page = page.replace(
    "// every byte value below is copied verbatim from a real Sweetwater specimen\n"
    "  // (e3_bass.krz for the bank objects, verbhall.krz for SROM).",
    "// every byte value below is copied verbatim from a real file:\n"
    "  // a 44.1 kHz stereo sidecar, 7.774 s of source audio, written by Live 12.")

stale = [w for w in ("Kurzweil", "krz", "PRAM", "SROM", "e3_bass", "Sweetwater",
                     "K2000", "VAST") if w in page]
if stale:
    raise SystemExit(f"template leftovers still in the page: {stale}")

OUT.write_text(page, encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
