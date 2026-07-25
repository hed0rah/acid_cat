# Game system audio formats: a landscape map

Every game system, arcade board, and handheld from the chip era to the present,
with a short technical readout of how each stores sound, and where acidcat sits
on it. It doubles as our coverage tracker for the disc-audio extraction path.

The line acidcat cares about is **extractable audio data**: PCM, ADPCM, and other
sample/stream codecs that live in files (or in known regions of a disc). The
chip/cartridge era mostly does not have that -- its "music" is a sequence of
register writes to a synthesis chip (a program, not a recording), and only the
*samples* those programs trigger are extractable. Disc systems are where audio
becomes files, which is why the coverage below thickens from the CD era on.

## Status legend

| Status | Meaning |
|---|---|
| `SHIPPED` | acidcat decodes or extracts it today |
| `NEXT` | the planned next build (the capstone for this path) |
| `REACHABLE` | codec already in acidcat, or well documented -- needs wiring / a short RE |
| `HARD` | documented but a major undertaking (MDCT/keyed/proprietary containers) |
| `SEQUENCED` | music is register/sequence data, not a recording -- only triggered samples are extractable |
| `SCOPE` | out of scope for now (proprietary, licensed, or the cartridge sample tail) |

acidcat's codec inventory today: linear PCM (any width/endian), CD-DA (Red Book),
CD-XA ADPCM, PS1 SPU-ADPCM (VAG/VAB), IMA and Microsoft ADPCM, Nintendo
DSP-ADPCM, GameCube DTK ADPCM, CRI ADX, Amiga Fibonacci-delta (8SVX), NI NCW,
plus every tracker/sampler/preset format the walkers already cover.

---

## 1. Chip / cartridge era (mostly SEQUENCED)

Sound is a synthesis program. There are no audio files; a rip is a register log
(VGM, GYM, NSF, SPC, GBS) or a soundfont-like sample set. Only the sample data is
extractable, and it varies wildly per game and sound driver.

| System (year) | Sound hardware | Sample codec (if any) | Status |
|---|---|---|---|
| Atari 2600 (1977) | TIA (2 sq/noise) | none | SEQUENCED |
| Arcade (70s-80s) | AY-3-8910, SN76489, YM2151/2203, OKI MSM6295... | OKI ADPCM (4-bit VOX/Dialogic) | SEQUENCED / REACHABLE (OKI) |
| NES / Famicom (1983) | 2A03 APU (2 pulse, tri, noise, DMC) | DMC = 1-bit delta PCM | SEQUENCED |
| SMS / Game Gear (1985) | SN76489 PSG | none | SEQUENCED |
| PC Engine / TG16 (1987) | HuC6280 (6ch wavetable) | 5-bit sample playback (rare) | SEQUENCED |
| Genesis / Mega Drive (1988) | YM2612 FM + SN76489 | 8-bit PCM via the YM2612 DAC (ch 6), streamed by the driver | SEQUENCED / REACHABLE (raw PCM) |
| Game Boy / GBC (1989) | 4ch (2 pulse, 32-sample wave, noise) | 4-bit wave-RAM table | SEQUENCED |
| Neo Geo (cart, 1990) | YM2610 (FM + ADPCM-A x8 + ADPCM-B x1) | ADPCM-A/-B (4-bit) in the V-ROMs | REACHABLE (ADPCM-A/B documented) |
| SNES / Super Famicom (1990) | SPC700 + S-DSP (8ch) | **BRR** -- 4-bit ADPCM, 9-byte blocks (1 header + 8 data = 16 samples), fixed 4 filters | REACHABLE (BRR is small and well documented) |
| GBA (2001) | 2 Direct Sound 8-bit PCM + 4 PSG | raw 8-bit PCM; sound drivers vary (Sappy/M4A, Krawall, GAX...) | SCOPE (per-driver, heuristic) |
| N64 (1996) | RSP audio microcode | **VADPCM** -- vector-quantized 4-bit ADPCM, per-instrument codebook (`.ctl`/`.tbl`, ALBank) | REACHABLE (VADPCM + ALBank documented) |

---

## 2. The CD-DA era (audio becomes files)

CD add-ons and the first optical consoles. All carry Red Book **CD-DA** (raw
16-bit LE stereo 44100, extracted straight off audio tracks -- `SHIPPED` for all
of these via the `.cue` path), plus a system-specific ADPCM/PCM for in-game sound.

| System (year) | Streamed / in-game audio | Status |
|---|---|---|
| PC Engine CD / TG-CD (1988) | CD-DA + ADPCM (OKI MSM5205-family, 4-bit) | CD-DA SHIPPED; ADPCM REACHABLE |
| Sega CD / Mega CD (1991) | CD-DA + RF5C164 PCM (8-channel sample chip) | CD-DA SHIPPED; RF5C164 PCM REACHABLE |
| Philips CD-i (1991) | **CD-XA ADPCM** (same family as PS1) | SHIPPED (our XA decoder) |
| 3DO (1993) | CD-DA + AIFF-C, usually **SDX2** (square-delta ADPCM) | CD-DA SHIPPED; AIFF walker present; SDX2 REACHABLE |
| Amiga CD32 (1993) | CD-DA + Amiga samples (8SVX / raw) | CD-DA + 8SVX SHIPPED |
| Neo Geo CD (1994) | CD-DA (arranged soundtracks) + YM2610 ADPCM-A/B from disc | CD-DA SHIPPED; ADPCM-A/B REACHABLE |

---

## 3. Fifth generation (disc)

| System (year) | Audio formats | Status |
|---|---|---|
| Sega Saturn (1994) | CD-DA + SCSP/YMF292 PCM (32ch) + Saturn ADPCM streams | CD-DA SHIPPED; SCSP PCM / ADPCM REACHABLE |
| **PlayStation** (1994) | **CD-XA ADPCM** (`.STR`/`.XA` streams), **SPU-ADPCM** (VAG samples, VAB banks, `.VB`/`.BD`), CD-DA | SHIPPED (all three, banks split per-sample via the VAB header) |
| N64 (1996) | cartridge -- VADPCM (see chip era) | REACHABLE |

---

## 4. Sixth generation (disc)

| System (year) | Audio formats | Status |
|---|---|---|
| Dreamcast (1998) | **CRI ADX**, **AICA ADPCM** (Yamaha 4-bit), CD-DA (GD-ROM) | ADX + CD-DA SHIPPED; AICA REACHABLE |
| **PlayStation 2** (2000) | SPU2-ADPCM (VAG; interleaved `.int`/`.mib`/`.ss2`), CRI ADX/AHX, many game containers | VAG + ADX SHIPPED; interleaved streams REACHABLE; AHX HARD (MPEG) |
| **GameCube** (2001) | **DSP-ADPCM** (`.dsp`, HAL `.hps`, `.ast`/AFC), **DTK** (`.adp`), **CRI ADX** | HPS + ADX + DTK + DSP SHIPPED; AFC/AST REACHABLE |
| Xbox (2001) | **Xbox-ADPCM** (a Microsoft-ADPCM variant), XWMA, PCM | Xbox-ADPCM REACHABLE (close to our MS-ADPCM); WMA SCOPE |

---

## 5. Handhelds

| System (year) | Audio formats | Status |
|---|---|---|
| Game Boy line / GBA | see chip era | SEQUENCED / SCOPE |
| Nintendo DS (2004) | **SDAT** archive: SWAV/SWAR samples (PCM8/16 or **IMA ADPCM**), STRM streams (IMA ADPCM), SSEQ sequences | IMA ADPCM SHIPPED; SDAT/STRM container REACHABLE; SSEQ SEQUENCED |
| PSP (2004) | **VAG** (SPU-ADPCM), ATRAC3/ATRAC3plus (`.at3`, RIFF), CRI ADX/HCA | VAG + ADX SHIPPED; ATRAC3 HARD; HCA HARD |
| Nintendo 3DS (2011) | **BCSTM/BCWAV** (DSP-ADPCM, IMA, or PCM) | DSP + IMA SHIPPED (codecs); BCSTM container REACHABLE |
| PS Vita (2011) | ATRAC9 (`.at9`), CRI HCA, Wwise | HARD (ATRAC9 / HCA) |

---

## 6. Seventh generation and beyond

Modern consoles move to perceptual codecs (MDCT-based, often keyed) and
middleware wrappers. Increasingly `HARD` or `SCOPE`, with the exceptions noted.

| System (year) | Audio formats | Status |
|---|---|---|
| Xbox 360 (2005) | XMA/XMA2 (WMA-Pro based), XWMA, PCM | XMA HARD; PCM SHIPPED |
| PlayStation 3 (2006) | ATRAC3/AT3+, MP3, AC3, CRI HCA, Bink | MP3 SHIPPED; ATRAC/HCA/AC3 HARD |
| **Wii** (2006) | **BRSTM / BRWAV / BRWSD / `.ast`** -- Nintendo **DSP-ADPCM** (or PCM) in a RIFF-like container | **NEXT** (the codec is SHIPPED; BRSTM is mostly container parsing) |
| Wii U (2012) | **BFSTM / BFWAV** -- DSP-ADPCM | REACHABLE (same codec, newer container) |
| Nintendo Switch (2017) | BFSTM (DSP-ADPCM), custom **Opus**, CRI HCA, Wwise | DSP REACHABLE; Opus REACHABLE-ish; HCA/Wwise HARD |
| PS4 / PS5 (2013/2020) | ATRAC9, Vorbis (Wwise), MP3, HCA | Vorbis/MP3 REACHABLE; ATRAC9/HCA HARD |
| Xbox One / Series (2013/2020) | XMA (early), Wwise Vorbis/Opus, WMA | HARD (Wwise/XMA) |

---

## 7. Cross-platform middleware

Middleware appears on many systems at once, so cracking one reaches a large
catalogue. This is where the biggest remaining wins (and the hardest ones) sit.

| Middleware | What it is | Status |
|---|---|---|
| **CRI ADX** | Fixed-coefficient 4-bit ADPCM, predictor from the highpass cutoff | SHIPPED |
| CRI AHX | MPEG-2 Layer II in an ADX-style header | HARD (MPEG) |
| **CRI HCA** | MDCT-based perceptual codec, keyed/scrambled -- the ADX successor, on nearly everything post-2010 (`.hca`, `.acb`/`.awb`) | HARD -- **the big frontier**; the natural boundary for this path |
| FMOD FSB (3/4/5) | Container; inside: PCM, IMA/MS ADPCM, **FADPCM** (FMOD's own), MP3, Vorbis, AT9, Opus | Container + FADPCM REACHABLE; Vorbis/AT9/Opus varies |
| Audiokinetic Wwise (`.wem`/`.bnk`) | RIFF wrapper over custom-setup **Vorbis**, Opus, PCM, ADPCM, XMA, AT9 | HARD (Wwise Vorbis needs codebook reconstruction) |
| RAD Bink Audio (`.bik`/`.bka`) | DCT-based, documented but intricate | HARD |
| RAD Miles (`.mss`) | Wrapper over MP3 / ADPCM / Bink | REACHABLE (parts) |

---

## 8. acidcat coverage and roadmap

**Shipped:** the full 5th/6th-gen disc-audio core -- PS1 (CD-XA, SPU banks split
per-sample, CD-DA), CD-DA for every `.cue` disc (Sega CD, Neo Geo CD, PC-Engine,
Saturn), GameCube (DSP-ADPCM, HAL HPS, DTK, CRI ADX), and CRI ADX cross-platform.
Plus the standalone codec surface (IMA/MS ADPCM, tracker/sampler/preset formats).

**Next (the capstone):** **Wii BRSTM/BFSTM.** It is DSP-ADPCM -- which we already
decode -- in a RIFF-like container, so it is almost entirely container parsing for
a large coverage jump (all of Wii, and Wii U / 3DS / Switch share the family).

**Reachable, to round out the disc era** (each reuses a codec we have or is a
short, well-documented RE): Dreamcast AICA ADPCM; Xbox-ADPCM; PS2 interleaved SPU
streams; Nintendo DS SDAT/STRM (IMA); 3DS BCSTM; GameCube AFC/AST; SNES BRR;
N64 VADPCM; OKI/RF5C/YM2610 samples for the CD add-ons.

**The frontier (HARD):** CRI **HCA** is the single highest-reach remaining codec
(MDCT + key scrambling, everywhere post-2010), and it marks the honest boundary
for a "disc-era complete" stopping point. Beyond it lie Wwise Vorbis, XMA,
ATRAC9, and Bink -- each a substantial, largely-proprietary undertaking.

**Explicitly out of scope:** chip-era sequenced music (a synthesis program, not a
recording -- only its triggered samples are data), and the per-game cartridge
sound drivers (GBA, most 8/16-bit) where extraction is heuristic rather than a
format.

### Maintaining this file

When a format lands, flip its row to `SHIPPED` and update section 8. When a new
specimen reveals a format not listed, add its row with the best-known technical
readout and a status. Autocorrelation on the decoded output is the truth test we
use before calling a codec `SHIPPED`.
