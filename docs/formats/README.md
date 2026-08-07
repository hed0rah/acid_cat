# The anatomy pages exist in two places

These files are also published at
<https://hed0rah.github.io/audio_files_anatomy/>, from the `audio_files_anatomy`
directory of the `hed0rah.github.io` repository. **The published site is
canonical.** This directory is a mirror.

That is not an aesthetic preference. Design work happens on the site -- the
theme system in particular -- and it lands there first.

## Why this file exists

The two copies drifted, with real edits in **both** directions, and nothing
detected it. When they were finally compared, the site was a full generation
ahead on presentation (five theme modes and a theme-aware favicon, against the
mirror's none) and also carried content corrections the mirror lacked:

| | the mirror had | the site had | correct |
|---|---|---|---|
| sf2 root key 76 | E5 | E4 | **site** -- acidcat's own `midi_note_to_name` uses C3=60 |
| sigmf `r`/`c` prefix | optional | required | **site** -- the walker's regex rejects `i16_le` |
| mp4 lower-case fourCC | user extensions | standard types | **site** |
| amiga MED `[12..15]` | one reserved word | `psecnum` / `pseq` | **site** |
| 8svx envelope pairs | (duration, volume) | (duration word, 32-bit Fixed volume) | **site** |
| rx2 child chunks | `SINF` + `SDAT`, `SLCE` nested | `SLCE` + `SDAT` | **mirror** -- confirmed on three specimens |
| mp3 LAME preset | 2 bytes, with surround info | id only | **mirror** -- confirmed against LAME's `VbrTag.c` |

Both copies were wrong about something, and each was right about something the
other had wrong. A one-way sync in either direction would have destroyed real
work.

## Keeping them in step

- Edit the **site**, then copy down here.
- Before copying in either direction, **diff the content with styling
  stripped**. A raw diff is useless: the theme CSS and toggle are ~7 KB per
  page and bury any real change.
- Write with byte-level operations. Line endings differ per file on the site
  (`mp3-anatomy.html` is LF, `index.html` is CRLF), and going through Python's
  text layer rewrites every line -- a three-line correction became a 1,100-line
  diff that way.
- Run `python scripts/check_bytemaps.py <dir>` against both copies. It asserts
  every byte in every map is claimed by exactly one field, and it takes seconds.
- A new page is generated from an existing one so the shell cannot drift:
  `python scripts/build_ableton_anatomy.py <src-page> <out-page>`. Generate
  from the **site's** shell, not this one, or the page ships without the
  current themes.

## The standard the pages are held to

Every byte in a byte map comes from a real, named specimen, and is verified
back against that file. Two pages have failed this: the tracker IMPS map
disagreed with its own specimen at three bytes, and the serum page described a
capability (`zstd` frame identification) that does not exist in the code.
