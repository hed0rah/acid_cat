# acidcat cheatsheet

A low-level audio and preset metadata tool. readelf/exiftool for audio.

## commands

| command | does |
|---|---|
| `acidcat FILE` | quick info for one file (bare path auto-routes to `info`) |
| `acidcat DIR` | scan a directory (auto-routes to `scan`) |
| `acidcat info FILE` | format, duration, key, bpm, tags (uses mutagen where it helps) |
| `acidcat formats` | what this build supports, per capability (walk / extract / convert / repair) |
| `acidcat classify FILE` | what is this? magic + structure, without committing to a walker |
| `acidcat inspect FILE...` | readelf-style structural dump (see flags below) |
| `acidcat chunks FILE` | RIFF chunk table (offsets, sizes, parsed fields) |
| `acidcat dump FILE CHUNK` | hex-dump a named chunk |
| `acidcat od FILE` | annotated, colored hex dump of any bytes (`--offset` `--at` `--region`) |
| `acidcat shape FILE...` | one structural fingerprint per file, built for `sort \| uniq -c` |
| `acidcat cover FILE` | extract, embed or remove embedded cover art (`-o` `--set` `--remove`) |
| `acidcat survey DIR` | count chunk types across a tree |
| `acidcat census DIR` | chunk-ID histogram + open-question flags over a tree (parallel, `--jobs`) |
| `acidcat detect FILE\|DIR` | estimate bpm/key with librosa |
| `acidcat features DIR` | extract 50+ audio features (ML) |
| `acidcat scan DIR` | batch scan to CSV |
| `acidcat index DIR` | upsert into the global SQLite index |
| `acidcat query [flags]` | filter the index by bpm/key/tag/text |
| `acidcat query --compatible-with FILE` | samples that mix with FILE (key + tempo, `--same-key` `--bpm-tolerance` `--kind`) |
| `acidcat similar FILE` | sounds like FILE over the index (`index --features` first); `-n` `--kind` `--no-kind-filter` `--paths-only` |
| `acidcat convert FILE` | export/transcode: bwclip -> MIDI, NCW/8SVX -> WAV, SF2/SF3 -> samples; `--to-pcm` decodes ADPCM/mistagged WAV to plain PCM (`--codec ima` forces it) |
| `acidcat probe FILE read AT\|scan V\|find HEX\|strings\|hexdump AT\|diff F2` | byte dissection (RE surface): typed read, value scan, pattern find, strings, hexdump, diff; AT can be an offset or `chunk`/`chunk.field` |
| `acidcat locate BLOB` | find audio regions in a blob/disk image (containers + raw PCM + headerless MP3); `--mode`, `--analyze`, `--transforms`, `-v`. Pipe `--json` to `carve --batch` |
| `acidcat extract BANK` | pull every embedded sample out of a bank/module as WAVs (MOD/XM/IT/S3M, `.pat`, 8SVX, NCW, SF2/SF3, `.multisample`, `.krz`, `.e4b`/`.e5b`, `.snd`) |
| `acidcat carve FILE --chunk ID\|--trailing\|--offset N\|--batch SRC` | extract a byte region (chunk / appended blob / range) to a file; `--batch` cuts every `locate` region into a dir; `--wrap` puts a WAV header on raw PCM |
| `acidcat wrap < raw.pcm` | filter: wrap raw PCM bytes in a WAV header (`--rate` `--channels` `--bits` `--endian` `--float`) |
| `acidcat repair FILE` | fix stale sizes, offset tables, counts, pad bytes (audio untouched, keeps a backup) |
| `acidcat validate FILE\|DIR` | read-only structural check, exit 0 clean / 1 broken |
| `acidcat audit FILE` | forensic verdict: structure, integrity (fake hi-res, duration), hidden data, provenance |
| `acidcat tui FILE` | interactive inspector (goto/search, follow pointers, byte map, edit, validate/repair) |
| `acidcat write FILE --set k=v` | edit metadata (backup + `-o` + `--dry-run`); Bitwig/NI presets (experimental) |
| `acidcat --version` | version |

Read from stdin: `acidcat -` or `cat f.wav | acidcat`.

## inspect flags

```
acidcat inspect FILE... [--json] [--pretty] [--hex] [--frames]
                        [--only IDS] [--exclude IDS] [--full] [--color auto|always|never]
                        [--format FMT] [--force] [--resync] [--region N]
```

| flag | effect |
|---|---|
| (default) | readelf-style table: chunk map, decoded fields, lint warnings |
| `--pretty` | human-friendly metadata view, no byte offsets (best for presets/tags) |
| `--hex` | raw bytes beside each decoded field |
| `-F`, `--frames` | per-element deep dump (every MPEG frame / MIDI event) |
| `--only fmt,bext` | show only these chunks (case-insensitive); compose with `--hex` |
| `--exclude data` | hide these chunks |
| `--full` | self-contained JSON dump (raw region bytes + absolute field offsets) |
| `--anomalies` | forensic scan: trailing data, polyglots, cavities, size mismatches, LSB-stego notice |
| `-v`, `--verbose` | synonym for `--frames` (Bitwig device tree + parameters + notes, Vital modulation matrix, NI hsin FastLZ subtree) |
| `--json` | JSON output; multiple files become NDJSON (one record per line) |
| `--color` | auto (TTY) / always / never; honors NO_COLOR |
| `--format FMT` | parse as FMT regardless of the magic bytes (old/odd variants) |
| `--force` | on a file no walker claims, try them all and report what each made of it |
| `--resync` | rebuild chunk structure from a damaged container by scanning for `[id][size]` records |
| `--region N` | walk the Nth region `locate` reported, inside a larger blob |
| multiple files | each under a `File:` banner |

## what this build supports

Do not trust a list in a doc -- ask the binary:

```
acidcat formats                 # the capability matrix: walk / extract / convert / repair
acidcat formats wav             # one format
acidcat formats --json | jq -r '.[] | select(.extract) | .id'
```

Broadly: audio containers (WAV/RIFF, RF64, AIFF/AIFC, FLAC, MP3, MIDI, RMID,
Ogg/Opus, MP4/M4A), sampler and tracker banks (SF2/SF3, `.mod`/`.xm`/`.it`/`.s3m`,
E-mu, Akai, Kurzweil, MPC, 8SVX, NCW), console formats (SNES BRR, N64 ALBank,
Wii/GameCube BRSTM), and synth/DAW presets (Serum, Bitwig, Vital, Native
Instruments, VST FXP, ReCycle RX2).

## repair / validate / audit (the constraint model)

A container is a set of derived fields (sizes, offsets, counts, pad bytes) whose
correct value is a function of the data. `validate` reports the ones that don't
match; `repair` fixes the witnessed ones; `audit` adds forensics + provenance.
Audio is never touched; `repair` keeps a `_original` backup.

```
acidcat validate DIR              # sweep a tree, exit 1 if any file is broken
acidcat repair broken.wav         # fix stale riff_size / cue count / pad byte
acidcat audit suspect.wav         # STRUCTURE / INTEGRITY / HIDDEN / PROVENANCE
acidcat audit file.wav --json     # machine-readable verdict
```

## recipes

```
# just the tags/metadata, prettily
acidcat inspect --pretty track.m4a
acidcat inspect --pretty MyPatch.bwpreset

# hexdump one chunk
acidcat inspect --only fmt --hex loop.wav

# machine-readable, many files, into jq
acidcat inspect --json *.wav | jq -c '.chunks[].id'

# build a standalone interactive byte explorer for any file
acidcat explore song.mp3 -o song.html

# per-frame MP3 bitrate switching / per-event MIDI
acidcat inspect --frames song.mp3
acidcat inspect --frames beat.mid

# index a library, then query it
acidcat index ~/samples
acidcat query --bpm 120:130 --key Am

# pull the notes out of a Bitwig clip as MIDI
acidcat convert MyClip.bwclip -o MyClip.mid
acidcat query --device Polysynth --category Reverb   # search preset metadata
acidcat query --product Vital --creator someone
```

## recovery / rescue

`locate` finds audio in a raw blob; `carve` cuts it out; `extract` unpacks a known
bank; `convert --to-pcm` makes odd codecs playable. Full workflow in
[docs/recovery.md](docs/recovery.md).

```
# find audio regions in a disk image / card dump (never writes)
acidcat locate disk.img --mode aggressive --analyze
dd if=/dev/sdcard | acidcat locate -                 # straight off a device

# the pipeline: locate every region, carve each into a directory
acidcat locate disk.img --json | acidcat carve disk.img --batch - -o recovered/

# pull every sample out of a sampler bank / tracker module
acidcat extract kit.sf2 -o kit_samples/

# make an ADPCM / mistagged WAV play anywhere
acidcat convert weird.wav --to-pcm -o plain.wav
acidcat convert mistagged.wav --to-pcm --codec ima   # force IMA on a wrong tag

# CTF: audio hidden under a reversible transform (XOR / rotate / nibble-swap)
acidcat locate challenge.bin --transforms
```

## install / upgrade

```
pipx install acidcat          # first time
pipx upgrade acidcat          # get the newest (reinstall does NOT upgrade)
pip install -U acidcat        # with pip
pip install -e .              # editable, from a checkout (runs live source)
pip install -e .[mcp]         # + MCP stdio server (acidcat-mcp)
pip install -e .[mcp-http]    # + MCP streamable-HTTP transport (acidcat-mcp --transport http)
pip install -e .[all]         # everything
```

## edit / write metadata

    acidcat write FILE... --set field=value [--set ...] [-o OUT] [--dry-run]

WYSIWYG: the fields `inspect --pretty` shows are the fields you edit. In-place by
default after a `<name>_original` backup; `-o` writes a copy; `--dry-run` shows
the diff and writes nothing; multiple files = batch. Atomic (never a half file).

    # tag an audio file (wav/mp3/flac/ogg/m4a)
    acidcat write loop.wav --set title="Deep Kick" --set artist="me" --set genre=Techno

    # set tempo + key on a WAV (writes the acid chunk)
    acidcat write loop.wav --set bpm=128 --set key=Am

    # sampler root note (smpl chunk) and broadcast-wav header fields
    acidcat write oneshot.wav --set root=C3
    acidcat write field.wav --set originator="me" --set bext_description="night frogs"

    # batch, preview first
    acidcat write *.wav --set genre=Foley --dry-run

    # rename a Vital preset / set its author
    acidcat write Bass.vital --set name="Reese Bass" --set author=me
