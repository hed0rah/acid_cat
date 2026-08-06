var R=[];
R.push({page:"ableton-anatomy.html",mount:"asd-hdr",unit:"byte",bytes:[0x06,0x49,0x49,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0xAB,0x00,0x00,0x00,0xAE,0x03,0x00,0x00],fields:[
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
      ]});
R.push({page:"ableton-anatomy.html",mount:"asd-name",unit:"byte",bytes:[0x0B,0x00,0x00,0x00,0x57,0x00,0x61,0x00,0x72,0x00,0x70,0x00,0x4D,0x00,0x61,0x00,0x72,0x00,0x6B,0x00,0x65,0x00,0x72,0x00,0x73,0x00],fields:[
        {label:"char_count",k:"sync",r:[0,3],val:"11",
          body:"Little-endian u32: the length of the name in CHARACTERS, not bytes. The name occupies twice this many bytes.",
          note:"This prefix is what separates a real field name from an accidental run of ASCII-range UTF-16, and it is the whole basis of the name scan."},
        {label:"name (UTF-16LE)",k:"enum",r:[4,25],val:"\"WarpMarkers\"",
          body:"The field name, UTF-16 little-endian. Every other byte is 0x00, which is exactly why an ASCII string scan finds nothing here and the format reads as opaque.",
          note:"69 distinct field names were recovered across the specimen set. Together they are a map of everything Live records about the audio."}
      ]});
R.push({page:"akai-anatomy.html",mount:"akai-riff",unit:"byte",bytes:[0x52,0x49,0x46,0x46, 0x00,0x00,0x00,0x00, 0x41,0x50,0x52,0x47],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"RIFF\"",body:"The RIFF container signature, little-endian family. acidcat routes on RIFF + the APRG form type."},
        {label:"riff_size",k:"flag",r:[4,7],val:"0",body:"little-endian u32: the RIFF body size -- but Akai writes 0 here rather than the real length, a firmware quirk. acidcat falls back to the actual file size and walks the chunks anyway.",note:"0x00000000; a strict reader trusting this would read nothing."},
        {label:"form_type",k:"sync",r:[8,11],val:"\"APRG\"",body:"The RIFF form type: APRG = Akai program. This is what distinguishes an .akp from any other RIFF file."}
      ]});
R.push({page:"akai-anatomy.html",mount:"akai-prg",unit:"byte",bytes:[0x70,0x72,0x67,0x20, 0x06,0x00,0x00,0x00, 0x01, 0x04, 0x09, 0x00,0x02,0x00],fields:[
        {label:"chunk_id",k:"sync",r:[0,3],val:"\"prg \"",body:"The program-header chunk id (trailing space). First chunk after the RIFF header."},
        {label:"chunk_size",k:"sync",r:[4,7],val:"6",body:"little-endian u32: the prg payload size, 6 bytes.",note:"0x00000006 = 6."},
        {label:"version",k:"rsv",r:[8,8],val:"1",body:"prg byte 0: a structure/version constant, 1 in S5000/S6000 programs."},
        {label:"midi_program",k:"enum",r:[9,9],val:"4",body:"prg byte 1: the MIDI program number the sampler assigns to this program.",note:"acidcat surfaces it as midi_program."},
        {label:"keygroups",k:"enum",r:[10,10],val:"9",body:"prg byte 2: the DECLARED number of keygroups. acidcat compares it to the count of kgrp chunks it actually walks and warns on a mismatch.",note:"9 here, matching the 9 kgrp chunks."},
        {label:"reserved",k:"rsv",r:[11,13],val:"00 02 00",body:"The remaining prg bytes -- program-wide flags, not decoded field by field."}
      ]});
R.push({page:"akai-anatomy.html",mount:"akai-kloc",unit:"byte",bytes:[0x6B,0x6C,0x6F,0x63, 0x10,0x00,0x00,0x00, 0x01,0x03,0x01,0x04, 0x58, 0x7F, 0x00,0x00],fields:[
        {label:"chunk_id",k:"sync",r:[0,3],val:"\"kloc\"",body:"The key-location chunk id: the first inner chunk of a kgrp's nested IFF stream."},
        {label:"chunk_size",k:"sync",r:[4,7],val:"16",body:"little-endian u32: the kloc payload size, 16 bytes.",note:"0x00000010 = 16."},
        {label:"params",k:"rsv",r:[8,11],val:"01 03 01 04",body:"kloc leading bytes: keygroup flags (mono/poly, mute group, ...). Surfaced, not split field by field."},
        {label:"low_key",k:"enum",r:[12,12],val:"88",body:"The lowest MIDI note this keygroup answers to. 0x58 = 88.",note:"paired with high_key to form the keygroup's key window."},
        {label:"high_key",k:"enum",r:[13,13],val:"127",body:"The highest MIDI note this keygroup answers to. 0x7F = 127. So this keygroup covers notes 88-127.",note:"the sampler picks a keygroup by which window contains the played note."},
        {label:"pad",k:"rsv",r:[14,15],val:"00 00",body:"Trailing kloc bytes, zero here."}
      ]});
R.push({page:"akai-anatomy.html",mount:"akai-zone",unit:"byte",bytes:[0x7A,0x6F,0x6E,0x65, 0x2E,0x00,0x00,0x00, 0x01, 0x0C, 0x31,0x32,0x20,0x53,0x54,0x52,0x49,0x4E,0x47,0x20,0x47,0x35],fields:[
        {label:"chunk_id",k:"sync",r:[0,3],val:"\"zone\"",body:"A velocity-zone chunk id. Up to four per keygroup."},
        {label:"chunk_size",k:"sync",r:[4,7],val:"46",body:"little-endian u32: the zone payload size, 46 bytes (the name plus tuning/level params).",note:"0x0000002E = 46."},
        {label:"flag",k:"rsv",r:[8,8],val:"1",body:"zone byte 0: a presence/mode flag. 1 = an active zone."},
        {label:"name_len",k:"enum",r:[9,9],val:"12",body:"zone byte 1: the length of the sample name that follows. 0x0C = 12. A length of 0 marks an empty zone, which acidcat skips.",note:"length-prefixed, not null-terminated."},
        {label:"sample_name",k:"flag",r:[10,21],val:"\"12 STRING G5\"",body:"The name of the external .wav this zone plays. The program references it; the audio lives in a sibling file. acidcat collects every distinct zone name as the program's sample dependency list.",note:"12 ASCII bytes; the zone's tuning/level params follow (not drawn)."}
      ]});
R.push({page:"amiga-anatomy.html",mount:"smus-form",unit:"byte",bytes:[0x46,0x4F,0x52,0x4D, 0x00,0x00,0x0A,0xDE, 0x53,0x4D,0x55,0x53],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"FORM\"",body:"The IFF group signature -- the same wrapper AIFF and 8SVX use. acidcat routes on FORM + the SMUS form type."},
        {label:"form_size",k:"sync",r:[4,7],val:"2,782",body:"BIG-endian u32: the FORM body size, everything after this pair. IFF sizes are big-endian; the Amiga is a 68000.",note:"0x00000ADE = 2782."},
        {label:"form_type",k:"sync",r:[8,11],val:"\"SMUS\"",body:"The FORM type: SMUS = Sonix musical score. This is what tells it apart from an AIFF or 8SVX FORM."}
      ]});
R.push({page:"amiga-anatomy.html",mount:"smus-shdr",unit:"byte",bytes:[0x53,0x48,0x44,0x52, 0x00,0x00,0x00,0x04, 0x4A,0x8B, 0x7C, 0x05],fields:[
        {label:"chunk_id",k:"sync",r:[0,3],val:"\"SHDR\"",body:"The score-header chunk id, first inside the FORM."},
        {label:"chunk_size",k:"sync",r:[4,7],val:"4",body:"BIG-endian u32: the SHDR payload size, 4 bytes.",note:"0x00000004 = 4."},
        {label:"tempo",k:"flag",r:[8,9],val:"0x4A8B",body:"BIG-endian u16: the raw SMUS tempo word (a Sonix-internal unit, not BPM directly). Surfaced as the raw value.",note:"the score's playback speed."},
        {label:"volume",k:"enum",r:[10,10],val:"124",body:"The score master volume. 0x7C = 124."},
        {label:"ctTrack",k:"enum",r:[11,11],val:"5",body:"The number of note tracks (TRAK chunks) in the score. 5 here.",note:"one TRAK chunk follows per track."}
      ]});
R.push({page:"amiga-anatomy.html",mount:"okt-magic",unit:"byte",bytes:[0x4F,0x4B,0x54,0x41,0x53,0x4F,0x4E,0x47],fields:[
        {label:"magic",k:"sync",r:[0,7],val:"\"OKTASONG\"",body:"The 8-byte Oktalyzer signature -- a single token, no version field. acidcat sniffs it to route the file; the IFF-style chunks begin immediately after."}
      ]});
R.push({page:"amiga-anatomy.html",mount:"okt-cmod",unit:"byte",bytes:[0x43,0x4D,0x4F,0x44, 0x00,0x00,0x00,0x08, 0x00,0x01, 0x00,0x01, 0x00,0x01, 0x00,0x01],fields:[
        {label:"chunk_id",k:"sync",r:[0,3],val:"\"CMOD\"",body:"The channel-mode chunk id."},
        {label:"chunk_size",k:"sync",r:[4,7],val:"8",body:"BIG-endian u32: the CMOD payload size, 8 bytes = four u16 words.",note:"0x00000008 = 8."},
        {label:"channel[0]",k:"enum",r:[8,9],val:"1 (split)",body:"BIG-endian u16 for hardware channel 0. Non-zero = split into two voices; 0 = one voice. 1 here.",note:"a split word doubles this channel."},
        {label:"channel[1]",k:"enum",r:[10,11],val:"1 (split)",body:"Channel 1 split flag. 1 = two voices."},
        {label:"channel[2]",k:"enum",r:[12,13],val:"1 (split)",body:"Channel 2 split flag. 1 = two voices."},
        {label:"channel[3]",k:"enum",r:[14,15],val:"1 (split)",body:"Channel 3 split flag. 1 = two voices. All four split -> 8 voices total, the Oktalyzer signature.",note:"acidcat sums 2-per-split, 1-per-single to report the voice count."}
      ]});
R.push({page:"amiga-anatomy.html",mount:"med-hdr",unit:"byte",bytes:[0x4D,0x4D,0x44,0x30, 0x00,0x01,0xC5,0x7A, 0x00,0x00,0x00,0x34, 0x00,0x00,0x00,0x00],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"MMD0\"",sel:0,branch:[["MMD0","MED"],["MMD1","OctaMED"],["MMD2","OctaMED Pro"],["MMD3","OctaMED SoundStudio"]],
          body:"The MED/OctaMED signature. The last digit steps with the software generation, which acidcat maps straight to the variant name.",note:"MMD0 = the original MED."},
        {label:"modlen",k:"enum",r:[4,7],val:"116,090",body:"BIG-endian u32: the declared module length. acidcat checks it against the real file size and warns on a mismatch -- a truncation tell.",note:"0x0001C57A = 116090, matching the file."},
        {label:"song_ptr",k:"flag",r:[8,11],val:"52",body:"BIG-endian u32: an absolute file offset to the song structure -- the first of the pointer table that makes MED pointer-based rather than chunked.",note:"0x00000034 = 52."},
        {label:"reserved",k:"rsv",r:[12,15],val:"0",body:"The next header word, 0 here. The full pointer table (patterns, samples, expansion) continues; acidcat summarises at the header."}
      ]});
R.push({page:"amiga-anatomy.html",mount:"fc-hdr",unit:"byte",bytes:[0x46,0x43,0x31,0x34, 0x00,0x00,0x03,0xDC, 0x00,0x00,0x04,0x90, 0x00,0x00,0x04,0xC0],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"FC14\"",sel:0,branch:[["SMOD","Future Composer 1.3"],["FC14","Future Composer 1.4"]],
          body:"The Future Composer signature. SMOD is v1.3, FC14 is v1.4 -- a synth-driver chiptune, not a sample tracker.",note:"acidcat maps the magic to the version."},
        {label:"seq_length",k:"enum",r:[4,7],val:"988",body:"BIG-endian u32: the sequence-table length in bytes -- the first of the region sizes a player sums to locate each section.",note:"0x000003DC = 988."},
        {label:"pattern_offset",k:"flag",r:[8,11],val:"1,168",body:"BIG-endian u32: the offset (or length) of the pattern region. Part of the offset table FC uses in place of chunks.",note:"0x00000490 = 1168."},
        {label:"pattern_length",k:"flag",r:[12,15],val:"1,216",body:"BIG-endian u32: the next region word. acidcat surfaces the header; the waveform/frequency-table decode is deferred.",note:"0x000004C0 = 1216."}
      ]});
R.push({page:"bfdlac-anatomy.html",mount:"bfdc-hdr",unit:"byte",bytes:[0x42,0x46,0x44,0x43, 0x00,0x0D,0xD4,0x3E],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"BFDC\"",body:"The BFD-compressed signature, ASCII. acidcat sniffs these four bytes to route the file to the bfdlac walker."},
        {label:"outer_size",k:"enum",r:[4,7],val:"906,302",body:"BIG-endian u32: the IFF outer size, everything after this 8-byte magic+size pair = file length - 8. acidcat checks it against the real length and flags a mismatch.",note:"0x000DD43E = 906302; file is 906310 bytes."}
      ]});
R.push({page:"bfdlac-anatomy.html",mount:"bfdc-fmt",unit:"byte",bytes:[0x66,0x6D,0x74,0x20, 0x00,0x00,0x00,0x14, 0x00,0x00,0x00,0x18, 0x00,0x00,0x00,0x0A, 0x00,0x03,0x5D,0xA1, 0x00,0x00,0xAC,0x44, 0x00,0x00,0x00,0x02],fields:[
        {label:"chunk_id",k:"sync",r:[0,3],val:"\"fmt \"",body:"The audio-descriptor chunk id (note the trailing space). IFF chunk framing: a 4-byte id, a big-endian u32 size, then the payload."},
        {label:"chunk_size",k:"sync",r:[4,7],val:"20",body:"BIG-endian u32: the fmt payload size, 20 bytes (five u32 fields).",note:"0x00000014 = 20."},
        {label:"bits_per_sample",k:"enum",r:[8,11],val:"24",body:"BIG-endian u32: the audio bit depth. 24-bit across the entire BFD corpus.",note:"0x00000018 = 24."},
        {label:"encoding",k:"flag",r:[12,15],val:"10",body:"BIG-endian u32: a codec/encoding tag. Constant 10 in every file the walker has seen -- an id for the lac stream, not a variable.",note:"0x0000000A = 10."},
        {label:"num_samples",k:"enum",r:[16,19],val:"220,577",body:"BIG-endian u32: frames per channel. At 44100 Hz that is 5.00 seconds.",note:"0x00035DA1 = 220577."},
        {label:"sample_rate",k:"enum",r:[20,23],val:"44100 Hz",body:"BIG-endian u32: the sample rate. 44100 across the corpus.",note:"0x0000AC44 = 44100."},
        {label:"channels",k:"enum",r:[24,27],val:"2 (stereo)",body:"BIG-endian u32: the channel count. Stereo across the corpus.",note:"0x00000002 = 2."}
      ]});
R.push({page:"bfdlac-anatomy.html",mount:"bfdc-indx",unit:"byte",bytes:[0x49,0x6E,0x64,0x78, 0x00,0x00,0x03,0x68, 0x00,0x00,0x04,0x00, 0x00,0x00,0x00,0xD8, 0x00,0x00,0x00,0x00, 0x00,0x00,0x24,0x70],fields:[
        {label:"chunk_id",k:"sync",r:[0,3],val:"\"Indx\"",body:"The seek-index chunk id."},
        {label:"chunk_size",k:"sync",r:[4,7],val:"872",body:"BIG-endian u32: the Indx payload size. After the 8-byte block/frame header, the rest is the u32 offset table -- (872 - 8) / 4 = 216 entries.",note:"0x00000368 = 872."},
        {label:"block_size",k:"enum",r:[8,11],val:"1024",body:"BIG-endian u32: samples per compressed block. Each Indx entry points at one such block in the data chunk.",note:"0x00000400 = 1024."},
        {label:"frame_count",k:"enum",r:[12,15],val:"216",body:"BIG-endian u32: the number of blocks. Must equal ceil(num_samples / block_size) = ceil(220577 / 1024) = 216; acidcat cross-checks it.",note:"0x000000D8 = 216."},
        {label:"offset[0]",k:"flag",r:[16,19],val:"0",body:"BIG-endian u32: byte offset of block 0 into the data chunk. Always 0 -- the first block opens the payload."},
        {label:"offset[1]",k:"flag",r:[20,23],val:"9,328",body:"BIG-endian u32: byte offset of block 1. Compression makes block boundaries unpredictable, which is exactly why they are tabulated.",note:"0x00002470 = 9328; the table continues for all 216 blocks."}
      ]});
R.push({page:"bfdlac-anatomy.html",mount:"bfdc-data",unit:"byte",bytes:[0x64,0x61,0x74,0x61, 0x00,0x0D,0xD0,0x7E, 0x0A,0x76,0x2D,0xEF, 0xFF,0x98,0x95,0x4D],fields:[
        {label:"chunk_id",k:"sync",r:[0,3],val:"\"data\"",body:"The audio chunk id -- the last and largest chunk, so the walker stops here."},
        {label:"chunk_size",k:"enum",r:[4,7],val:"905,342",body:"BIG-endian u32: the compressed-stream size. 905,342 of the file's 906,310 bytes -- the audio is essentially the whole file.",note:"0x000DD07E = 905342."},
        {label:"lac_stream",k:"rsv",r:[8,15],val:"compressed",body:"The lossless (lac) codec stream begins here. High-entropy (~7 bits/byte), no readable structure; acidcat surfaces the region but does not decode it -- the codec is undocumented.",note:"0A 76 2D EF FF 98 95 4D ... the compressed payload."}
      ]});
R.push({page:"emu-anatomy.html",mount:"e5b-form",unit:"byte",bytes:[0x46,0x4f,0x52,0x4d,0x00,0x0a,0x35,0xb4,0x45,0x35,0x42,0x30],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"FORM\"",body:"The IFF container magic. Both E4B and E5B open with it."},
        {label:"form_size",k:"sync",r:[4,7],val:"669,108",body:"Total size of the form content, big-endian u32. E5B follows standard IFF: filesize - 8. (E4B uses filesize - 12, excluding the form-type.)",note:"669,108 + 8 = 669,116 = the file size."},
        {label:"form_type",k:"enum",r:[8,11],val:"\"E5B0\"",body:"Emulator X / Proteus X bank. The E4 -> E5 bump in this tag marks the EOS-hardware to software jump.",note:"E4B0 here would be an EOS hardware bank."}
      ]});
R.push({page:"emu-anatomy.html",mount:"toc2-entry",unit:"byte",bytes:[0x45,0x35,0x50,0x31,0x00,0x00,0x09,0xc8,0x00,0x00,0x02,0x84,0x00,0x00,0x54,0x00,0x52,0x00,0x55,0x00,0x43,0x00,0x4b,0x00],fields:[
        {label:"tag",k:"sync",r:[0,3],val:"\"E5P1\"",body:"The tag of the chunk this entry points to (E5P1 preset, E5S1 sample, or E5SL link)."},
        {label:"data_size",k:"sync",r:[4,7],val:"2504",body:"The pointed chunk's data size, big-endian u32."},
        {label:"file_offset",k:"enum",r:[8,11],val:"0x284",body:"Absolute offset of the chunk's tag, big-endian u32. Authoritative -- a reader can seek straight here. acidcat cross-checks every TOC offset against the linear chunk chain; a mismatch flags corruption.",note:"the same check pinpointed a broken E4B repair fixture."},
        {label:"index",k:"flag",r:[12,13],val:"0",body:"A 0-based index within the chunk's kind, big-endian u16."},
        {label:"name",k:"enum",r:[14,23],val:"\"TRUCK1SPRING\"",body:"UTF-16LE name, a 64-byte field (32 code units) running to byte 77. It starts at 0x0e -- decoding from 0x0c hits the index's null and returns empty.",note:"only the head of the 64-byte name is shown."}
      ]});
R.push({page:"emu-anatomy.html",mount:"e5p1-head",unit:"byte",bytes:[0x00,0x00,0x50,0x68,0x64,0x72,0x00,0x00,0x00,0x8e,0x00,0x00,0x00,0x01,0x54,0x00,0x52,0x00,0x55,0x00,0x43,0x00,0x4b,0x00],fields:[
        {label:"count / flag",k:"rsv",r:[0,1],val:"0",body:"A leading count/flag word; 0 observed. The nested sub-chunks begin at byte 2."},
        {label:"sub-tag",k:"sync",r:[2,5],val:"\"Phdr\"",body:"The first sub-chunk tag: the preset header. E5P1 is a nested IFF container -- typically nine sub-chunks (Phdr, E5IC, E5CL, E5MP, EXPs, and four LISTs)."},
        {label:"sub-size",k:"sync",r:[6,9],val:"142",body:"The Phdr sub-chunk's data size, big-endian u32."},
        {label:"phdr lead",k:"rsv",r:[10,13],val:"1",body:"A leading field inside Phdr (00000001); the name follows at sub-body offset 4."},
        {label:"name",k:"enum",r:[14,23],val:"\"TRUCK1SPRING\"",body:"The preset name, UTF-16LE, inside Phdr. This is the only E5P1 field acidcat decodes today.",note:"voice/zone data lives in the LIST sub-chunks -- Phase 2."}
      ]});
R.push({page:"emu-anatomy.html",mount:"e5s1-hdr",unit:"byte",bytes:[0x00,0x00,0x00,0x00,0x01,0x00,0x54,0x00,0x52,0x00,0x55,0x00,0x43,0x00,0x4b,0x00,0x31,0x00,0x53,0x00,0x50,0x00,0x52,0x00],fields:[
        {label:"reserved",k:"rsv",r:[0,3],val:"0",body:"Leading zero word."},
        {label:"flag",k:"flag",r:[4,5],val:"0x0001",body:"A flag word; 0x0001 observed across the corpus."},
        {label:"name",k:"enum",r:[6,23],val:"\"TRUCK1SPRING\"",body:"UTF-16LE sample name, a 64-byte field starting at 0x06. The raw bytes interleave with nulls -- it is UTF-16LE, not the UTF-8 some converters assume.",note:"rate, loop points, and bit depth follow (see 'later fields')."}
      ]});
R.push({page:"emu-anatomy.html",mount:"e5sl",unit:"byte",bytes:[0x00,0x01,0x00,0x00,0x00,0x01],fields:[
        {label:"slot",k:"sync",r:[0,1],val:"1",body:"The link slot, big-endian u16; sequential within the bank."},
        {label:"sample_index",k:"enum",r:[2,5],val:"1",body:"A 1-based index into the SamplePool, big-endian u32. Resolves to a sibling .ebl sample -- banks carry no PCM of their own.",note:"the PCM lives in SamplePool/*.ebl."}
      ]});
R.push({page:"emu-anatomy.html",mount:"e4b-form",unit:"byte",bytes:[0x46,0x4f,0x52,0x4d,0x00,0x00,0x21,0x5e,0x45,0x34,0x42,0x30],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"FORM\"",body:"The IFF container magic."},
        {label:"form_size",k:"sync",r:[4,7],val:"8,542",body:"Big-endian u32 = filesize - 12. The E-MU quirk: it excludes the 4-byte form-type, so it is 4 smaller than standard IFF would write.",note:"8,542 + 12 = 8,554 = the file size."},
        {label:"form_type",k:"enum",r:[8,11],val:"\"E4B0\"",body:"Emulator 4 / EOS hardware bank."}
      ]});
R.push({page:"emu-anatomy.html",mount:"e3s1-hdr",unit:"byte",bytes:[0x00,0x01,0x53,0x49,0x4e,0x45,0x5f,0x41,0x5f,0x41,0x32,0x20,0x20,0x20,0x20,0x20,0x20,0x20],fields:[
        {label:"sample_index",k:"sync",r:[0,1],val:"1",body:"1-based sample index, big-endian u16. TOC1 entries and zone references point at this."},
        {label:"name",k:"enum",r:[2,17],val:"\"SINE_A_A2\"",body:"16-byte ASCII name, space-padded (E4 is ASCII; E5 widened to UTF-16). The trailing _<note><octave> suffix encodes the sample's root note for hardware browsers.",note:"numeric fields follow: rate (u32 LE) at 0x36, a loop word at 0x3c, loop start/end offsets."}
      ]});
R.push({page:"flac-anatomy.html",mount:"block-hdr",unit:"bit",bytes:[0x84,0x00,0x01,0x30],fields:[
    {label:"last-block flag",k:"flag",r:[0,0],val:"1 (last)",sel:1,branch:[["0","more blocks follow"],["1","final metadata block"]],
      body:"The high bit of byte 0. When set, the audio frames begin right after this block.",note:"a walk that never sees this set is malformed; acidcat lints it."},
    {label:"block type",k:"enum",r:[1,7],val:"4 = VORBIS_COMMENT",sel:4,branch:[
      ["0","STREAMINFO"],["1","PADDING"],["2","APPLICATION"],["3","SEEKTABLE"],
      ["4","VORBIS_COMMENT"],["5","CUESHEET"],["6","PICTURE"],["127","invalid"]],
      body:"Seven bits naming the block. STREAMINFO (0) must come first; this one is the tag block. Type 127 is forbidden, not merely unused: with the last-block flag set the header byte would be 0xFF, and the audio frame sync also opens 0xFF -- reserving 127 stops a metadata header from masquerading as a frame."},
    {label:"length",k:"enum",r:[8,31],val:"304 bytes",
      body:"24-bit big-endian payload size that follows this 4-byte header. 0x000130 = 304.",
      note:"checked against the bytes that remain; an overrun is linted, not trusted."}
  ]});
R.push({page:"flac-anatomy.html",mount:"block-si",unit:"byte",bytes:[0x10,0x00,0x10,0x00,0x00,0x00,0x0E,0x00,0x20,0x00,0x0A,0xC4,0x42,0xF0,0x00,0x01,0x58,0x88,
     0xA0,0xA1,0xA2,0xA3,0xA4,0xA5,0xA6,0xA7,0xA8,0xA9,0xAA,0xAB,0xAC,0xAD,0xAE,0xAF],fields:[
      {label:"min_block_size",k:"enum",r:[0,1],val:"4096",body:"Smallest block size in samples, u16 big-endian."},
      {label:"max_block_size",k:"enum",r:[2,3],val:"4096",body:"Largest block size. Equal to min for fixed-block streams."},
      {label:"min_frame_size",k:"enum",r:[4,6],val:"14 bytes",body:"Smallest frame in bytes, u24. 0 means unknown."},
      {label:"max_frame_size",k:"enum",r:[7,9],val:"8192 bytes",body:"Largest frame in bytes, u24."},
      {label:"packed",k:"enum",r:[10,17],val:"44100 / 2ch / 16-bit / 88200",sel:0,branch:[
        ["20 bits","sample rate = 44100 Hz"],["3 bits","channels - 1 = 1  ->  2"],
        ["5 bits","bits/sample - 1 = 15  ->  16"],["36 bits","total samples = 88200"]],
        body:"A bit-packed 64-bit field, not byte-aligned: rate, channels-1, bits-1, then a 36-bit total sample count.",
        note:"duration = total_samples / sample_rate, no frame walk needed. channels and bits store minus one. The 20-bit rate holds any value up to 1,048,575 Hz (0 is invalid); frame headers re-code it in just 4 bits with escapes back to here."},
      {label:"md5_signature",k:"enum",r:[18,33],val:"16-byte MD5",body:"MD5 of the unencoded audio, so a decoder can verify a bit-exact round trip. 0 = unset."}
    ]});
R.push({page:"fxp-anatomy.html",mount:"block-head",unit:"byte",bytes:[0x43,0x63,0x6E,0x4B, 0x00,0x00,0x00,0x64, 0x46,0x50,0x43,0x68, 0x00,0x00,0x00,0x01, 0x58,0x66,0x73,0x58, 0x00,0x00,0x00,0x01, 0x00,0x00,0x00,0x01],fields:[
      {label:"magic",k:"sync",r:[0,3],val:'\'"CcnK"\'',
       body:"The container signature at offset 0. Steinberg\'s VST 2 chunk magic; the same word opens .fxb banks.",
       note:"43 63 6E 4B."},
      {label:"byte_size",k:"sync",r:[4,7],val:"100",
       body:"uint32 BIG-endian: bytes that follow this field, so it equals the total file size minus 8 (the magic + byte_size ahead of it are excluded). Big-endian is the classic FXP trap on a little-endian host; a wrong reading yields garbage.",
       note:"00 00 00 64 = 0x64 = 100."},
      {label:"fxMagic",k:"enum",r:[8,11],val:'\'"FPCh"\'',sel:1,branch:[
        ["FxCk","regular preset (float params)"],["FPCh","opaque-chunk preset"],
        ["FxBk","regular bank"],["FBCh","opaque-chunk bank"]],
       body:"The preset kind. FxCk stores a list of float parameters; FPCh (here) stores an opaque plugin blob the plugin serializes itself.",
       note:"FPCh -> the payload is length-prefixed opaque bytes."},
      {label:"version",k:"rsv",r:[12,15],val:"1",
       body:"uint32 BE: FXP format version. Rarely meaningful."},
      {label:"plugin_id",k:"enum",r:[16,19],val:'\'"XfsX"\'',
       body:"The FourCC the plugin registers. XfsX = Xfer Serum, NiMs = NI Massive, syle = Sylenth1. This is how you tell which synth wrote the preset without opening it.",
       note:"a chosen id, not standardized across vendors."},
      {label:"plugin_version",k:"enum",r:[20,23],val:"1",
       body:"uint32 BE: the plugin\'s own version stamp."},
      {label:"num_params",k:"enum",r:[24,27],val:"1",
       body:"uint32 BE. In a single-program .fxp (an fxProgram) this field is num_params, the count of float parameters the plugin exposes, NOT a program count; num_programs only exists in a .fxb bank (fxSet). This example header reads 1."}
    ]});
R.push({page:"gf1pat-anatomy.html",mount:"gf1-sig",unit:"byte",bytes:[0x47,0x46,0x31,0x50,0x41,0x54,0x43,0x48,0x31,0x31,0x30,0x00, 0x49,0x44,0x23,0x30,0x30,0x30,0x30,0x30,0x32,0x00],fields:[
        {label:"magic",k:"sync",r:[0,11],val:"\"GF1PATCH110\"",sel:0,branch:[["GF1PATCH110","version 1.10"],["GF1PATCH100","version 1.00 (older)"]],
          body:"The patch signature, ASCII, 12 bytes including a trailing null. The last three digits are the format version.",
          note:"acidcat sniffs the first 8 bytes (\"GF1PATCH\") to route the file, then reads the full 12 for the exact version."},
        {label:"gravis_id",k:"rsv",r:[12,21],val:"\"ID#000002\"",body:"A fixed 10-byte internal structure id, \"ID#000002\", the same in every patch. Not a per-file value.",note:"present so a loader could version the on-disk struct layout; constant in practice."}
      ]});
R.push({page:"gf1pat-anatomy.html",mount:"gf1-counts",unit:"byte",bytes:[0x01, 0x04, 0x0E, 0x03,0x00, 0x7F,0x00, 0xE0,0x2B,0x00,0x00],fields:[
        {label:"instruments",k:"enum",r:[0,0],val:"1",body:"Number of top-level instrument records that follow the header. The walker loops this many times.",note:"absolute file offset 82."},
        {label:"voices",k:"rsv",r:[1,1],val:"4",body:"A card-mixing hint (active voice count), not structural. Fixed/quirk in most factory patches.",note:"offset 83."},
        {label:"channels",k:"rsv",r:[2,2],val:"14",body:"Another mixing hint. Carries a constant quirk value (14) across the factory set; acidcat surfaces it without interpreting.",note:"offset 84."},
        {label:"waveforms",k:"enum",r:[3,4],val:"3",body:"little-endian u16: the TOTAL sample (waveform) count across all layers -- a cross-check for the walk. This patch has 3.",note:"0x0003; offset 85-86."},
        {label:"master_volume",k:"flag",r:[5,6],val:"127",body:"little-endian u16: the patch master volume, 0-127.",note:"0x007F; offset 87-88."},
        {label:"data_size",k:"enum",r:[7,10],val:"11,232",body:"little-endian u32: the summed PCM byte total across every sample in the patch.",note:"0x00002BE0 = 11232; offset 89-92."}
      ]});
R.push({page:"gf1pat-anatomy.html",mount:"gf1-inst",unit:"byte",bytes:[0x00,0x00, 0x41,0x6C,0x74,0x6F,0x20,0x53,0x61,0x78,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00, 0xE0,0x2B,0x00,0x00, 0x01],fields:[
        {label:"instrument_id",k:"enum",r:[0,1],val:"0",body:"little-endian u16: the instrument's numeric id within the patch. 0 for the first (and here only) instrument."},
        {label:"instrument_name",k:"sync",r:[2,17],val:"\"Alto Sax\"",body:"16-byte ASCII instrument name, null-padded. What a librarian or MIDI mapper shows; the patch is referenced by file, not by this name."},
        {label:"instrument_size",k:"enum",r:[18,21],val:"11,232",body:"little-endian u32: total bytes of this instrument (all its layers, sample headers, and PCM).",note:"0x00002BE0 = 11232, matching the patch data_size for a single-instrument file."},
        {label:"layers",k:"enum",r:[22,22],val:"1",body:"The layer count. Each layer is a velocity/key split with its own set of samples. One here.",note:"the next 40 bytes of the 63-byte record are reserved padding."}
      ]});
R.push({page:"gf1pat-anatomy.html",mount:"gf1-layer",unit:"byte",bytes:[0x00, 0x00, 0xE0,0x2B,0x00,0x00, 0x03],fields:[
        {label:"layer_duplicate",k:"rsv",r:[0,0],val:"0",body:"A flag for a duplicated layer (voice doubling). 0 in this patch."},
        {label:"layer_id",k:"enum",r:[1,1],val:"0",body:"The layer index within the instrument. 0 = the first layer."},
        {label:"layer_size",k:"enum",r:[2,5],val:"11,232",body:"little-endian u32: total bytes of this layer, its sample headers plus their PCM.",note:"0x00002BE0 = 11232."},
        {label:"samples",k:"enum",r:[6,6],val:"3",body:"The number of sample records in this layer -- the innermost count the walker iterates. Each is a 96-byte header followed by its PCM.",note:"the remaining 40 bytes of the 47-byte record are reserved."}
      ]});
R.push({page:"gf1pat-anatomy.html",mount:"gf1-sample",unit:"byte",bytes:[0x4E,0x6F,0x4E,0x61,0x6D,0x65,0x00, 0x00, 0xE4,0x1B,0x00,0x00, 0x6E,0x17,0x00,0x00, 0xAA,0x1B,0x00,0x00, 0x31,0x57, 0x6A,0x69,0x00,0x00, 0x95,0x19,0x06,0x00, 0xCD,0x83,0x02,0x00, 0x00,0x02, 0x06, 0x3F,0x3F,0xC4,0x8D,0x8D,0xD6, 0xE8,0xF3,0xF6,0xC0,0xD0,0x08, 0x00,0xFF,0x32, 0x28,0xC8,0x08, 0x67, 0x3C,0x00, 0x00,0x04],fields:[
        {label:"wave_name",k:"sync",r:[0,6],val:"\"NoName\"",body:"7-byte ASCII sample name, null-padded. Factory patches often leave it \"NoName\"."},
        {label:"fractions",k:"rsv",r:[7,7],val:"0",body:"Sub-byte remainder for the loop points -- fine loop tuning below one byte. 0 here.",note:"the high/low nibbles refine loop_start / loop_end."},
        {label:"data_size",k:"enum",r:[8,11],val:"7,140",body:"little-endian u32: the length of this sample's raw PCM, in bytes, sitting immediately after this 96-byte header.",note:"0x00001BE4 = 7140 bytes -> 3570 samples at 16-bit."},
        {label:"loop_start",k:"enum",r:[12,15],val:"5,998",body:"little-endian u32: loop start as a BYTE offset into this sample's PCM (honoured only when modes & 0x04).",note:"0x0000176E = 5998."},
        {label:"loop_end",k:"enum",r:[16,19],val:"7,082",body:"little-endian u32: loop end as a byte offset. Playback loops over [loop_start, loop_end].",note:"0x00001BAA = 7082."},
        {label:"sample_rate",k:"enum",r:[20,21],val:"22321 Hz",body:"little-endian u16: the PCM playback sample rate.",note:"0x5731 = 22321."},
        {label:"low_frequency",k:"flag",r:[22,25],val:"26.986 Hz",body:"little-endian u32, milliHertz (Hz x 1000): the LOW edge of the pitch range this sample covers before a neighbouring sample takes over.",note:"0x0000696A = 26986 -> 26.986 Hz (~A0)."},
        {label:"high_frequency",k:"flag",r:[26,29],val:"399.765 Hz",body:"little-endian u32, milliHertz: the HIGH edge of this sample's pitch range.",note:"0x00061995 = 399765 -> 399.765 Hz (~G4)."},
        {label:"root_frequency",k:"enum",r:[30,33],val:"164.813 Hz",body:"little-endian u32, milliHertz: the pitch the sample was RECORDED at. The GF1 pitches each key by ratio from this, not from a MIDI note number.",note:"0x000283CD = 164813 -> 164.813 Hz (~E3)."},
        {label:"tune",k:"rsv",r:[34,35],val:"512",body:"little-endian i16: a fine-tune value. TiMidity and most players ignore it; surfaced, not applied.",note:"0x0200 = 512."},
        {label:"balance",k:"rsv",r:[36,36],val:"6",body:"Pan/balance, 0-15 with 7 = center. 6 here = very slightly left."},
        {label:"envelope_rate",k:"flag",r:[37,42],val:"6 stages",body:"Six bytes: the attack/decay/release RATES of the GF1's on-card 6-stage volume envelope, one per stage.",note:"3F 3F C4 8D 8D D6 -- the card plays this envelope in hardware."},
        {label:"envelope_offset",k:"flag",r:[43,48],val:"6 stages",body:"Six bytes: the target LEVELS the envelope ramps to at each of the six stages.",note:"E8 F3 F6 C0 D0 08."},
        {label:"tremolo",k:"rsv",r:[49,51],val:"sweep/rate/depth",body:"Three bytes: tremolo (amplitude LFO) sweep, rate, and depth.",note:"00 FF 32 = sweep 0, rate 255, depth 50."},
        {label:"vibrato",k:"rsv",r:[52,54],val:"sweep/rate/depth",body:"Three bytes: vibrato (pitch LFO) sweep, rate, and depth.",note:"28 C8 08 = sweep 40, rate 200, depth 8."},
        {label:"modes",k:"flag",r:[55,55],val:"0x67",sel:0,branch:[
          ["0x01","16-bit  ✔"],["0x02","unsigned  ✔"],["0x04","loop  ✔"],["0x08","ping-pong"],
          ["0x10","reverse"],["0x20","sustain  ✔"],["0x40","envelope  ✔"],["0x80","clamped"]],
          body:"One byte, eight independent flags. 0x67 = 16-bit + unsigned + loop + sustain + envelope. Bits 0 and 1 set the PCM width and sign -- get either wrong and the audio is noise.",
          note:"0x67 = 0110 0111."},
        {label:"scale_frequency",k:"enum",r:[56,57],val:"60 (C4)",body:"little-endian i16: the MIDI note at which key-tracking is centred for scale_factor tuning.",note:"0x003C = 60 = C4."},
        {label:"scale_factor",k:"enum",r:[58,59],val:"1024 (100%)",body:"little-endian u16: key-tracking amount in 1024ths. 1024 = 100% (normal chromatic tracking); 0 = fixed pitch.",note:"0x0400 = 1024. The 36 bytes after this are reserved."}
      ]});
R.push({page:"krz-anatomy.html",mount:"pram-hdr",unit:"byte",bytes:[0x50,0x52,0x41,0x4D, 0x00,0x00,0x04,0x04, 0x00,0x00,0x1F,0x14, 0x00,0x01,0xE0,0xE8, 0x00,0x00,0x00,0xD0, 0x03,0x10,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"PRAM\"",sel:0,branch:[["PRAM","object bank"],["SROM","effects / sample-ROM (see SROM tab)"]],
          body:"The bank signature, ASCII. \"PRAM\" is an object database; \"SROM\" is the effects/sample-ROM container.",
          note:"acidcat sniffs the first 4 bytes and routes either magic to the Kurzweil walker."},
        {label:"osize (pcm_offset)",k:"enum",r:[4,7],val:"1028",body:"BIG-endian i32: the absolute byte offset where the PCM sample region begins -- which is also the end of the object section. Written last, patched in after all objects are laid out.",
          note:"0x00000404 = 1028. If osize == filesize, the bank has objects but no samples."},
        {label:"reserved",k:"rsv",r:[8,11],val:"0x00001F14",body:"Reserved header word. The mpc2emu writer emits 0; this hardware-saved bank carries a non-zero value whose meaning is not pinned down.",note:"surfaced, never interpreted -- the walker does not guess."},
        {label:"reserved",k:"rsv",r:[12,15],val:"0x0001E0E8",body:"Reserved header word, likewise non-zero in hardware banks and 0 from mpc2emu."},
        {label:"software_version",k:"enum",r:[16,19],val:"208 (v2.08)",body:"BIG-endian i32: the K2000 OS version times 100. acidcat prints it as v2.08.",note:"0x000000D0 = 208. mpc2emu writes 353 (v3.53); the corpus ranges v1.00 .. v3.54."},
        {label:"reserved",k:"rsv",r:[20,31],val:"0x03100000..",body:"Trailing 12 reserved bytes. Zero from the writer; hardware banks vary. Not interpreted."}
      ]});
R.push({page:"krz-anatomy.html",mount:"object-hdr",unit:"byte",bytes:[0xFF,0xFF,0xFF,0xA4, 0x98,0xC8, 0x00,0x56, 0x00,0x0E, 0x55,0x4E,0x4E,0x41,0x4D,0x45,0x44,0x20,0x57,0x53, 0x00,0x00],fields:[
        {label:"blocksize",k:"sync",r:[0,3],val:"-92",body:"BIG-endian i32, stored NEGATIVE = -(total bytes of this block). A reader advances to the next object by adding -blocksize to its position; the walk stops at an int32 = 0.",
          note:"0xFFFFFFA4 = -92. This block is 92 bytes, so the next object is at pos + 92."},
        {label:"hash",k:"enum",r:[4,5],val:"0x98C8 (Sample #200)",sel:4,branch:[
          ["25","Master"],["28","Studio/FX"],["36","Program"],["37","Keymap"],["38","Sample"],["39","Setup"]],
          body:"BIG-endian u16 packing type and id: (type << 10) | id. Here 0x98C8 = (38 << 10) | 200 -> type 38 (Sample), id 200.",
          note:"type = hash >> 10, id = hash & 0x3FF. The three id spaces (sample/keymap/program) all start at 200; the type qualifies the id."},
        {label:"obj_size",k:"enum",r:[6,7],val:"86",body:"BIG-endian u16: the object size, from the size field to the end of the object body. 0x0056 = 86.",note:"distinct from blocksize, which counts the whole padded block."},
        {label:"name_ofs",k:"enum",r:[8,9],val:"14",body:"BIG-endian u16: offset from this field to the start of the object data = name_len + 3 (odd name) or + 4 (even). \"UNNAMED WS\" is 10 chars (even), so 14.",
          note:"the object body begins at this field's position + 14."},
        {label:"name",k:"sync",r:[10,19],val:"\"UNNAMED WS\"",body:"ASCII object name, up to 16 chars. Not how objects reference each other -- that is always by numeric id -- but what a librarian displays."},
        {label:"pad",k:"rsv",r:[20,21],val:"00 00",body:"Null terminator + word-alignment. One 0x00 for an odd-length name, two for even. Then the type-specific body begins."}
      ]});
R.push({page:"krz-anatomy.html",mount:"sample-body",unit:"byte",bytes:[0x00,0x01, 0x00,0x00, 0x00,0x08, 0x00, 0x00,0x00,0x00,0x00,0x00, 0x30, 0x70, 0x00,0x00, 0x14,0x24, 0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x01,0x35,0x06, 0x00,0x01,0x91,0x94, 0x00,0x08, 0x00,0x12, 0x00,0x00,0x63,0xE7],fields:[
        {label:"base_id",k:"rsv",r:[0,1],val:"1",body:"KSample header: always 1 in every real soundset. A structural constant, not a reference."},
        {label:"num_headers",k:"enum",r:[2,3],val:"0 (mono)",body:"Soundfilehead count minus one: 0 = one header (mono). A stereo sample sets this to 1 and appends a second Soundfilehead.",note:"mpc2emu and this specimen are mono-only."},
        {label:"headers_ofs",k:"rsv",r:[4,5],val:"8",body:"Offset to the first Soundfilehead. Always 8."},
        {label:"ks_flags",k:"flag",r:[6,6],val:"0x00 (mono)",body:"KSample flags. Bit 0 is the STEREO flag (not needsLoad). 0 here = mono."},
        {label:"reserved",k:"rsv",r:[7,11],val:"0x00..",body:"ks1 + copyID + ks2, all zero. Completes the 12-byte KSample header."},
        {label:"root_key",k:"enum",r:[12,12],val:"48 = C2",body:"Soundfilehead: the MIDI root note. The K2000 auto-transposes each key relative to this pitch.",note:"0x30 = 48; acidcat labels it C2."},
        {label:"sf_flags",k:"flag",r:[13,13],val:"0x70 = loop",sel:0,branch:[["0x70","looped"],["0xF0","one-shot"],["0x40 only","loads but silent (bug)"]],
          body:"Soundfilehead flags. 0x70 = looped, 0xF0 = one-shot. The loop on/off bit 0x80 is INVERTED: set = one-shot. The 0x10|0x20|0x40 bits are the load + playback-enable set.",
          note:"0x40 alone loads the sample but produces no sound -- the original silent-KRZ bug (HW-confirmed)."},
        {label:"vol_adjust",k:"rsv",r:[14,15],val:"0",body:"volumeAdjust + altVolumeAdjust, both 0."},
        {label:"max_pitch",k:"enum",r:[16,17],val:"5156",body:"BIG-endian u16, x100 cents: the pitch ceiling at which the sample, transposed up, hits the K2000's 48 kHz playback limit. Keys above it are not assigned the sample.",note:"0x1424 = 5156 -> ~root + 12*log2(48000/rate) semitones."},
        {label:"offset_to_name",k:"rsv",r:[18,19],val:"0",body:"Unused here, 0."},
        {label:"sample_start",k:"enum",r:[20,23],val:"0",body:"BIG-endian i32: absolute WORD offset of the sample's first PCM word. The byte address is osize + this*2.",note:"0 = the very start of the shared PCM region."},
        {label:"alt_sample_start",k:"rsv",r:[24,27],val:"0",body:"altSampleStart; equals sample_start in real files."},
        {label:"loop_start",k:"enum",r:[28,31],val:"79110",body:"BIG-endian i32: for a looped sample, the loop start as a PCM word offset.",note:"0x00013506 = 79110 words."},
        {label:"sample_end",k:"enum",r:[32,35],val:"102804",body:"BIG-endian i32: for a looped sample this is the LOOP END word, not the PCM end -- the K2000 loops over [loop_start, sample_end]. For a one-shot both collapse onto the PCM end.",note:"0x00019194 = 102804 words."},
        {label:"offset_to_env",k:"rsv",r:[36,37],val:"8",body:"offsetToEnvelope = 8 (mono, one header)."},
        {label:"alt_offset_to_env",k:"rsv",r:[38,39],val:"18",body:"altOffsetToEnvelope = 6 in the writer; 18 (0x12) in this specimen. Followed by two 12-byte Envelope structs (not drawn)."},
        {label:"sample_period",k:"enum",r:[40,43],val:"25575 -> 39101 Hz",body:"BIG-endian u32 = round(1e9 / sample_rate) in nanoseconds. There is no rate field; acidcat recovers rate = round(1e9 / period).",note:"0x000063E7 = 25575 -> 39101 Hz."}
      ]});
R.push({page:"krz-anatomy.html",mount:"keymap-hdr",unit:"byte",bytes:[0x00,0x00, 0x00,0x03, 0x00,0x00, 0x00,0x64, 0x00,0x7F, 0x00,0x03, 0x00,0x10,0x00,0x0E,0x00,0x0C,0x00,0x0A,0x00,0x08,0x00,0x06,0x00,0x04,0x00,0x02],fields:[
        {label:"sample_id",k:"enum",r:[0,1],val:"0",body:"Default sample id for the keymap. 0 for a multi-sample map -- the per-entry ids carry the actual mapping.",note:"the K2000 itself writes 0 here on save."},
        {label:"method",k:"enum",r:[2,3],val:"0x0003",sel:0,branch:[["0x0003","3-byte: sampleID | SSNr"],["0x0013","5-byte: tuning | sampleID | SSNr"]],
          body:"The per-entry format selector. 0x03 = 3-byte entries (sampleID + subsample, no tuning). 0x13 = 5-byte entries with a 2-byte tuning prefix -- the layout the modern K2000 and mpc2emu write.",
          note:"the 0x10 bit is what adds the tuning word. This 2013 bank uses 0x03; a reader that assumes 0x13 misreads the sample id (see the entry map)."},
        {label:"base_pitch",k:"rsv",r:[4,5],val:"0",body:"basePitch = 0 in every real production soundset."},
        {label:"cents_per_entry",k:"enum",r:[6,7],val:"100",body:"Cents between adjacent keys: 100 = one semitone per key. 0x64 = 100."},
        {label:"entries_per_vel",k:"enum",r:[8,9],val:"127",body:"NUM_KEYS - 1. 0x7F = 127."},
        {label:"entry_size",k:"enum",r:[10,11],val:"3",body:"Bytes per key entry -- the authoritative stride for walking the entries. 3 here (matching method 0x03); 5 for method 0x13.",note:"acidcat reads this field rather than assuming a width."},
        {label:"Level[8]",k:"rsv",r:[12,27],val:"velocity levels",body:"Eight big-endian i16 velocity-level words, (8 - j)*2 for j in 0..7 -- the single-velocity-level encoding a reader decodes back to one level spanning all 8 buckets.",note:"0x0010,0x000E,... = 16,14,12,10,8,6,4,2."}
      ]});
R.push({page:"krz-anatomy.html",mount:"keymap-entry",unit:"byte",bytes:[0x00,0xC8, 0x01],fields:[
        {label:"sample_id",k:"enum",r:[0,1],val:"200",body:"BIG-endian u16: the Sample object id this key plays. 0x00C8 = 200 -- Sample #200 from the Overview bank. This is the Keymap -> Sample edge of the reference graph.",
          note:"in a method-0x13 entry a 2-byte signed tuning precedes this, so the id sits at offset 2 instead of 0."},
        {label:"SSNr",k:"enum",r:[2,2],val:"1",body:"Subsample index within the sample, 1-based. 1 here."}
      ]});
R.push({page:"krz-anatomy.html",mount:"program-seg",unit:"byte",bytes:[0x08, 0x02,0x01,0x00,0x37,0x40,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00, 0x0F, 0x00,0x1E,0x2C,0x00,0x00,0x00,0x00],fields:[
        {label:"pgm_tag",k:"sync",r:[0,0],val:"0x08 PGM",body:"The program-globals segment tag. Segment length is keyed by tag & 0xF8: the 0x08 family is 15 data bytes.",note:"the stream is PGM FX once, then LYR...CAL HOB HOB HOB HOB per layer, ending at int16 = 0."},
        {label:"mode",k:"enum",r:[1,1],val:"2 = K2000",body:"PGM data byte 0: the synth mode. 2 = K2000."},
        {label:"num_layers",k:"enum",r:[2,2],val:"1",body:"PGM data byte 1: the layer count. One LYR segment, one CAL (and thus one keymap reference) per layer.",note:"acidcat also derives this by counting LYR (0x09) tags."},
        {label:"reserved",k:"rsv",r:[3,3],val:"0",body:"PGM data byte 2, unused here."},
        {label:"bend_range",k:"enum",r:[4,4],val:"0x37 = 55",body:"PGM data byte 3: the pitch-bend range."},
        {label:"portamento",k:"enum",r:[5,5],val:"0x40 = 64",body:"PGM data byte 4: portamento."},
        {label:"pgm_tail",k:"rsv",r:[6,15],val:"0x00..",body:"The rest of the 15-byte PGM segment, zero in this program."},
        {label:"fx_tag",k:"sync",r:[16,16],val:"0x0F FX",body:"The effect-selection segment tag: 7 data bytes. This is the exact-tag exception -- 0x0F is in the 0x08 family by the mask but is short, so a reader must check exact tags first.",note:"masking 0x0F & 0xF8 = 0x08 would over-read by 8 bytes and desync the walk."},
        {label:"fx_data",k:"enum",r:[17,23],val:"FX reference",body:"The 7-byte FX segment body -- the studio/effect the program routes through (the K2000 ROM effects, in a program that writes no type-28 object)."}
      ]});
R.push({page:"krz-anatomy.html",mount:"srom-hdr",unit:"byte",bytes:[0x53,0x52,0x4F,0x4D, 0x00,0x00,0x06,0x60],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"SROM\"",body:"The effects / sample-ROM signature, ASCII -- a different container from PRAM. acidcat recognizes it and surfaces the header instead of mis-parsing it as an object bank."},
        {label:"size",k:"enum",r:[4,7],val:"1632",body:"BIG-endian u32: the declared file size. Followed by a zero-filled body in this specimen; a full SROM decode is future work.",note:"0x00000660 = 1632 bytes."}
      ]});
R.push({page:"labx-anatomy.html",mount:"labx-zip",unit:"byte",bytes:[0x50,0x4B,0x03,0x04, 0x00,0x00, 0x00,0x00, 0x00,0x00, 0x55,0x44, 0xFA,0x5A, 0xA7,0x0C,0xD5,0x0E, 0x8B,0x14,0x00,0x00, 0x8B,0x14,0x00,0x00, 0x2A,0x00, 0x00,0x00],fields:[
        {label:"signature",k:"sync",r:[0,3],val:"PK 03 04",body:"The ZIP local-file-header signature. Every entry in the archive starts with it; acidcat routes the file as a zip on the leading PK."},
        {label:"version",k:"rsv",r:[4,5],val:"0",body:"little-endian u16: the ZIP version needed to extract. 0 for a plain stored entry."},
        {label:"flags",k:"rsv",r:[6,7],val:"0",body:"little-endian u16: general-purpose bit flags (encryption, streaming). 0 here."},
        {label:"method",k:"enum",r:[8,9],val:"0 (STORED)",body:"little-endian u16: the compression method. 0 = STORED (no compression) -- so the entry data is the literal Boost archive.",note:"Analog Lab writes every preset STORED."},
        {label:"mod_time",k:"rsv",r:[10,11],val:"0x4455",body:"little-endian u16: DOS modification time. Not used by acidcat."},
        {label:"mod_date",k:"rsv",r:[12,13],val:"0x5AFA",body:"little-endian u16: DOS modification date."},
        {label:"crc32",k:"flag",r:[14,17],val:"0x0ED50CA7",body:"little-endian u32: the CRC-32 of the entry data -- an integrity check on the stored preset.",note:"acidcat can verify it against the carved bytes."},
        {label:"comp_size",k:"enum",r:[18,21],val:"5,259",body:"little-endian u32: the compressed size. For a STORED entry it equals the uncompressed size.",note:"0x0000148B = 5259."},
        {label:"uncomp_size",k:"enum",r:[22,25],val:"5,259",body:"little-endian u32: the uncompressed size -- identical here, confirming STORED.",note:"0x0000148B = 5259."},
        {label:"name_len",k:"enum",r:[26,27],val:"42",body:"little-endian u16: the filename length. 42 = the length of the preset path that follows this header.",note:"0x002A = 42; e.g. Piano/User/'Solstice'/Low Notes - Solstice."},
        {label:"extra_len",k:"rsv",r:[28,29],val:"0",body:"little-endian u16: the extra-field length, 0 here. The data offset is 30 + name_len + extra_len past the header."}
      ]});
R.push({page:"labx-anatomy.html",mount:"labx-boost",unit:"byte",bytes:[0x32,0x32,0x20, 0x73,0x65,0x72,0x69,0x61,0x6C,0x69,0x7A,0x61,0x74,0x69,0x6F,0x6E,0x3A,0x3A,0x61,0x72,0x63,0x68,0x69,0x76,0x65, 0x20,0x31,0x30,0x20,0x30,0x20,0x37,0x20,0x30,0x20,0x37,0x20],fields:[
        {label:"len_prefix",k:"enum",r:[0,1],val:"22",body:"The length prefix of the first string: 22 bytes. Boost text serialization writes every string as its byte count, a space, then the bytes.",note:"22 = length of \"serialization::archive\"."},
        {label:"sp",k:"rsv",r:[2,2],val:"space",body:"The separator space between a length prefix and its string."},
        {label:"class_name",k:"sync",r:[3,24],val:"serialization::archive",body:"The Boost archive's class-name token -- the signature that identifies a text serialization archive. acidcat checks for it in the first 64 bytes to confirm the entry is a real preset archive.",note:"22 ASCII bytes."},
        {label:"version_sig",k:"flag",r:[25,36],val:"10 0 7 0 7",body:"The Boost library and class version integers, space-separated. After them come the preset's own fields -- name, bank, author, tags -- each a length-prefixed string.",note:"e.g. the next token is the preset name, written as its length then its bytes."}
      ]});
R.push({page:"midi-anatomy.html",mount:"block-mthd",unit:"byte",bytes:[0x4D,0x54,0x68,0x64, 0x00,0x00,0x00,0x06, 0x00,0x01, 0x00,0x03, 0x01,0xE0],fields:[
      {label:"magic",k:"sync",r:[0,3],val:"\"MThd\"",body:"The header chunk id. The whole file must open with these four bytes."},
      {label:"length",k:"enum",r:[4,7],val:"6",body:"Header byte count, u32 big-endian. Always 6; any extra bytes are legal and skipped."},
      {label:"format",k:"enum",r:[8,9],val:"1 = multi-track",sel:1,branch:[
        ["0","single track"],["1","multi-track, one timeline"],["2","independent patterns"]],
        body:"How the tracks relate. Format 1 is the common DAW export: track 0 holds tempo/meta, the rest hold parts on one shared timeline."},
      {label:"ntrks",k:"enum",r:[10,11],val:"3",body:"Number of MTrk chunks that follow, u16 big-endian."},
      {label:"division",k:"enum",r:[12,13],val:"480 ticks/quarter",sel:0,branch:[
        ["bit 15 = 0","ticks per quarter note (lower 15 bits)"],
        ["bit 15 = 1","SMPTE: high byte = signed -fps, low byte ticks/frame"]],
        body:"Timing resolution. The top bit selects the scheme; 0x01E0 has it clear, so the value is 480 ticks per quarter note.",
        note:"expanded bit by bit below. with no tempo event a player assumes 120 bpm."}
    ]});
R.push({page:"midi-anatomy.html",mount:"block-div",unit:"bit",bytes:[0x01,0xE0],fields:[
    {label:"scheme flag",k:"flag",r:[0,0],val:"0 = ticks/quarter",sel:0,branch:[
      ["0","ticks per quarter note"],["1","SMPTE frame timing"]],
      body:"The high bit of the 16-bit field. Clear here, so the remaining 15 bits are a tick count per quarter note.",
      note:"when set, the high byte is a signed 2's-complement frame rate (0xE8 = -24, 0xE7 = -25, 0xE3 = -29 meaning 29.97 drop-frame, 0xE2 = -30) and the low byte is ticks per frame."},
    {label:"ticks per quarter",k:"enum",r:[1,15],val:"480",
      body:"Pulses per quarter note (PPQN): how many delta-time ticks make one beat. 480 is a common DAW resolution.",
      note:"seconds per tick = (60 / bpm) / division, using the first tempo event."}
  ]});
R.push({page:"mp3-anatomy.html",mount:"block-frame",unit:"bit",bytes:[0xFF,0xFB,0x90,0xC0],fields:[
      {label:"frame sync",k:"sync",r:[0,10],val:"0x7FF",
       body:"Eleven set bits open every frame. Only 11 bits, so false positives happen; the whole header must validate before a sync is trusted.",
       note:"on a sync loss acidcat scans forward for the next valid header."},
      {label:"MPEG version",k:"enum",r:[11,12],val:"MPEG 1",sel:3,branch:[
        ["00","MPEG 2.5"],["01","reserved"],["10","MPEG 2"],["11","MPEG 1"]],
       body:"Selects the bitrate and rate tables and the samples-per-frame: 1152 for MPEG-1 layer III, 576 for MPEG-2/2.5.",
       note:"version 01 is reserved; acidcat rejects it as not a frame."},
      {label:"layer",k:"enum",r:[13,14],val:"Layer III",sel:1,branch:[
        ["00","reserved"],["01","Layer III"],["10","Layer II"],["11","Layer I"]],
       body:"Layer I/II/III select the frame-length formula. Layer III is the MP3."},
      {label:"protection",k:"flag",r:[15,15],val:"no CRC",sel:1,branch:[
        ["0","16-bit CRC after the header"],["1","no CRC"]],
       body:"When 0, a 2-byte CRC sits immediately after the 4-byte header. It protects only the last 2 header bytes and the side-information block, not the main audio data -- a bit error in the samples still passes clean."},
      {label:"bitrate index",k:"enum",r:[16,19],val:"128 kbps",sel:9,branch:[
        ["0","free"],["1","32"],["2","40"],["3","48"],["4","56"],["5","64"],["6","80"],
        ["7","96"],["8","112"],["9","128"],["10","160"],["11","192"],["12","224"],
        ["13","256"],["14","320"],["15","bad"]],
       body:"Index into the bitrate table for this version+layer (shown: MPEG-1 layer III, kbps). 0 is free-format and 15 is invalid; acidcat REJECTS only 15 and fully supports free format, measuring the frame length by sync spacing and reporting the derived kbps.",
       note:"frame_len = (samples/8) * bitrate / sample_rate + padding."},
      {label:"sample rate",k:"enum",r:[20,21],val:"44100 Hz",sel:0,branch:[
        ["0","44100"],["1","48000"],["2","32000"],["3","reserved"]],
       body:"Index into the MPEG-1 rate table (Hz). MPEG-2 is half, MPEG-2.5 a quarter.",
       note:"an 8000 Hz file forces MPEG 2.5 and 576 samples/frame."},
      {label:"padding",k:"flag",r:[22,22],val:"0",sel:0,branch:[
        ["0","none"],["1","+1 byte (+4 for layer I)"]],
       body:"A slack byte that keeps the average bitrate exact across frames."},
      {label:"private",k:"rsv",r:[23,23],val:"0",
       body:"Application-defined, ignored by decoders."},
      {label:"channel mode",k:"enum",r:[24,25],val:"mono",sel:3,branch:[
        ["00","stereo"],["01","joint stereo"],["10","dual channel"],["11","mono"]],
       body:"Mono here. The channel mode also sets the side-info size, which fixes where the Xing/Info header sits."},
      {label:"mode extension",k:"rsv",r:[26,27],val:"0",
       body:"Joint-stereo parameters; meaningful only when the mode is joint stereo."},
      {label:"copyright",k:"flag",r:[28,28],val:"0",
       body:"Copyright bit, informational."},
      {label:"original",k:"flag",r:[29,29],val:"0",
       body:"Original-media bit, informational."},
      {label:"emphasis",k:"enum",r:[30,31],val:"none",sel:0,branch:[
        ["00","none"],["01","50/15 ms"],["10","reserved"],["11","CCITT J.17"]],
       body:"Legacy de-emphasis curve; almost always none."}
    ]});
R.push({page:"mp4-anatomy.html",mount:"box-hdr",unit:"byte",bytes:[0x00,0x00,0x00,0x18,0x66,0x74,0x79,0x70],fields:[
    {label:"size",k:"sync",r:[0,3],val:"24 (0x00000018)",
      body:"Big-endian u32 giving the total byte count of this box, header included. 0x00000018 = 24. Special values: 0 means box extends to EOF; 1 means a 64-bit largesize u64 follows the type field at bytes 8-15, making the header 16 bytes total.",
      note:"A FullBox variant (mvhd, meta, stsd, etc.) appends a 1-byte version and 3-byte flags immediately after this 8-byte base header.",
      branch:[["0","box extends to end of file"],["1","64-bit largesize u64 follows at bytes 8-15"],["24 (0x18)","this box is 24 bytes total"]],sel:2},
    {label:"type (fourCC)",k:"sync",r:[4,7],val:'"ftyp"',
      body:'Four-character code identifying the box kind. 0x66747970 = "ftyp" in ASCII. The type drives how the payload is interpreted; unknown box types must be skipped using the size field, never aborted on.',
      note:"fourCC values containing lower-case letters are user-space extension boxes by convention. Type 'uuid' is the vendor escape hatch: a 16-byte user UUID follows the type, then vendor payload (XMP, PSP, and the like).",
      branch:[["ftyp","file type brand declaration"],["moov","movie container (metadata tree)"],["trak","track container (audio or video)"],["mdia","media information container"],["minf","media handler information"],["stbl","sample table (offsets, sizes, timestamps)"],["stsd","sample description / codec"],["mdat","media data (raw encoded samples)"],["mvhd","movie header: timescale and duration"],["free","free space (skip over payload)"],["udta","user data (iTunes tags)"],["meta","metadata container (FullBox)"]],sel:0}
  ]});
R.push({page:"mp4-anatomy.html",mount:"ftyp-pay",unit:"byte",bytes:[0x4D,0x34,0x41,0x20, 0x00,0x00,0x00,0x00, 0x4D,0x34,0x41,0x20, 0x6D,0x70,0x34,0x32],fields:[
      {label:"major_brand",k:"sync",r:[0,3],val:'"M4A "',
        body:'Primary specification for this file. 0x4D344120 = "M4A " -- note the trailing space, all four bytes are significant. Identifies the Apple/iTunes AAC audio profile.',
        note:'The ftyp box must be the first box in the file; its position implies no prior seek is needed to determine the file type.',
        branch:[["M4A ","Apple AAC audio profile (iTunes)"],["mp42","MPEG-4 Part 2"],["isom","ISO Base Media File Format v1"],["iso2","ISO Base Media File Format v2"],["qt  ","QuickTime (.mov)"],["dash","MPEG-DASH streaming"],["M4V ","iTunes video"],["m4b ","iTunes audiobook"]],sel:0},
      {label:"minor_version",k:"rsv",r:[4,7],val:"0",
        body:"Big-endian u32 informational minor version of the major brand specification. Typically 0 for M4A. Parsers must not use this to gate playback compatibility."},
      {label:"compatible_brands[0]",k:"enum",r:[8,11],val:'"M4A "',
        body:'First entry in the compatible brands list. A reader may play the file if it recognises any brand in this list. "M4A " here asserts the file fully satisfies the M4A profile.',
        note:"The list repeats to end of the ftyp box; each entry is exactly 4 bytes."},
      {label:"compatible_brands[1]",k:"enum",r:[12,15],val:'"mp42"',
        body:'Second compatible brand: MPEG-4 Part 2. A reader that implements mp42 can decode this file even without explicit M4A support. More brands may follow in larger ftyp boxes.'}
    ]});
R.push({page:"mpc-anatomy.html",mount:"snd-hdr",unit:"byte",bytes:[0x01,0x02,0x43,0x50,0x4d,0x37,0x5f,0x6b,0x31,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x00,0x64,0x00,0x01,0x00,0x00,0x00,0x00,0x68,0x1a,0x00,0x00,0x68,0x1a,0x00,0x00,0x00,0x00,0x00,0x00],fields:[
        {label:"validity",k:"sync",r:[0,0],val:"1",body:"A leading 1 marks a valid MPC2000 sound."},
        {label:"type",k:"enum",r:[1,1],val:"2 (compact)",sel:0,branch:[["2","compact export header (38 bytes)"],["4","classic hardware header (42 bytes)"]],body:"The type byte differs between exports and on-device files; acidcat accepts any value under 5 and resolves the header size by fit.",note:"classic hardware writes 4 here."},
        {label:"name",k:"enum",r:[2,17],val:"\"CPM7_k1\"",body:"16-byte sample name, space-padded, no extension."},
        {label:"pad",k:"rsv",r:[18,18],val:"0",body:"Unused."},
        {label:"level",k:"flag",r:[19,19],val:"100",body:"Playback level, 0-200 (100 = unity)."},
        {label:"tune",k:"flag",r:[20,20],val:"0",body:"Tune, signed, 10-cent steps."},
        {label:"channels",k:"enum",r:[21,21],val:"stereo",sel:1,branch:[["0","mono"],["1","stereo"]],body:"Discriminates perfectly across the kit. Stereo PCM is stored non-interleaved: the whole left block then the whole right.",note:"stereo stride = frames x 2 x 2 bytes."},
        {label:"start",k:"sync",r:[22,25],val:"0",body:"Sample start point, in frames (u32 LE)."},
        {label:"frame count",k:"enum",r:[26,29],val:"6760",body:"Sample length in frames per channel (u32 LE) -- acidcat reads it here and confirms it by fit. Per the classic-header docs this offset is the end point and 0x1e the frame count; they are equal in every unlooped specimen we have, so the two cannot be told apart from this file.",note:"38 + 6760 x 2 x 2 = 27078 = the file size, exactly."},
        {label:"length (copy)",k:"sync",r:[30,33],val:"6760",body:"A second copy of the length (the canonical frame count in the classic layout). Equal to the field above when there is no loop."},
        {label:"loop length",k:"rsv",r:[34,37],val:"0",body:"Loop length in frames; 0 = no loop (loop start = end - loop length). PCM begins at byte 38; the classic 42-byte header adds loop mode, beats, and sample rate before its PCM at byte 42."}
      ]});
R.push({page:"mpc-anatomy.html",mount:"pgm1000-hdr",unit:"byte",bytes:[0x04,0x2a,0x00,0x00,0x4d,0x50,0x43,0x31,0x30,0x30,0x30,0x20,0x50,0x47,0x4d,0x20,0x31,0x2e,0x30,0x30,0x00,0x00,0x00,0x00],fields:[
        {label:"file size",k:"sync",r:[0,1],val:"0x2A04",body:"Total file size (u16 LE) = 10756. A fixed-size format, so this doubles as a validity check.",note:"0x2A04 = 10756 bytes."},
        {label:"pad",k:"rsv",r:[2,3],val:"0",body:"Padding."},
        {label:"magic",k:"sync",r:[4,19],val:"\"MPC1000 PGM 1.00\"",body:"The 16-byte format signature. acidcat sniffs on this at offset 4; the MPC500/2500 share it."},
        {label:"pad",k:"rsv",r:[20,23],val:"0",body:"Header padding; the 64-pad array begins at byte 24."}
      ]});
R.push({page:"mpc-anatomy.html",mount:"pgm1000-lay",unit:"byte",bytes:[0x43,0x50,0x4d,0x37,0x5f,0x6b,0x31,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x82,0x40,0x00,0x64,0x46,0x7f,0x00,0x00,0x00,0x00],fields:[
        {label:"sample name",k:"enum",r:[0,15],val:"\"CPM7_k1\"",body:"16-byte name, NUL-terminated, referencing an external .WAV. An empty name means the layer is unused. Bytes after the terminator are uninitialized.",note:"acidcat reads to the first NUL."},
        {label:"pad",k:"rsv",r:[16,16],val:"0",body:"Padding."},
        {label:"level",k:"flag",r:[17,17],val:"100",body:"Layer level, 0-100."},
        {label:"vel lower",k:"enum",r:[18,18],val:"70",body:"Velocity range, lower bound (0-127). This layer covers loud hits, 70-127."},
        {label:"vel upper",k:"enum",r:[19,19],val:"127",body:"Velocity range, upper bound. Pad 0's second layer covers the softer 0-69 -- a velocity switch."},
        {label:"tune",k:"enum",r:[20,21],val:"0",body:"Tune, s16 LE, -3600..+3600 = -36..+36 semitones."},
        {label:"play mode",k:"flag",r:[22,22],val:"one-shot",sel:0,branch:[["0","one-shot"],["1","note-on"]],body:"One-shot plays to the end; note-on gates on the held note."},
        {label:"pad",k:"rsv",r:[23,23],val:"0",body:"Layer padding; the next layer follows at +0x18."}
      ]});
R.push({page:"mpc-anatomy.html",mount:"pgm2000-rec",unit:"byte",bytes:[0x43,0x50,0x4d,0x37,0x5f,0x6b,0x31,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x00],fields:[
        {label:"sample name",k:"enum",r:[0,15],val:"\"CPM7_k1\"",body:"16-byte, space-padded sample name -- a reference to a sibling .snd. The table starts at offset 2 and repeats until an empty name.",note:"this is the software-export form; hardware differs (see callout)."},
        {label:"terminator",k:"rsv",r:[16,16],val:"0",body:"One trailing byte after each name; the next record follows immediately."}
      ]});
R.push({page:"ogg-anatomy.html",mount:"page-hdr",unit:"byte",bytes:[0x4F,0x67,0x67,0x53,
     0x00,
     0x02,
     0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,
     0x01,0x00,0x00,0x00,
     0x00,0x00,0x00,0x00,
     0xCE,0x27,0x4A,0x11,
     0x01],fields:[
      {label:"capture_pattern",k:"sync",r:[0,3],val:'"OggS"',
        body:"Four-byte page sync marker present at the start of every OGG page. A reader scans for this sequence to locate or resync to a page boundary after corruption.",
        note:"0x4F 0x67 0x67 0x53 -- literal ASCII 'OggS'"},
      {label:"stream_structure_version",k:"rsv",r:[4,4],val:"0",
        body:"Must be 0 per RFC 3533. A value other than 0 indicates a future revision of the page format that this reader does not understand."},
      {label:"header_type",k:"flag",r:[5,5],val:"0x02 (BOS)",
        body:"Flag byte: bit 0 (0x01) = continued packet; bit 1 (0x02) = beginning of stream (BOS); bit 2 (0x04) = end of stream (EOS). See the bit register diagram below.",
        note:"the first page of every logical bitstream sets BOS; the last sets EOS; both may be set on a single-page stream",
        branch:[["0x01","continued packet -- first segment continues a packet from the previous page"],
                ["0x02","BOS -- beginning of stream, this page is first -- set here"],
                ["0x04","EOS -- end of stream, this page is last"]],
        sel:1},
      {label:"granule_position",k:"enum",r:[6,13],val:"-1 (no packet completes)",
        body:"Codec-specific position value, signed 64-bit little-endian. For Vorbis this is the PCM sample count of the last complete packet on the page. -1 (all 0xFF) means no codec packet completes on this page.",
        note:"-1 is typical for the BOS identification header page; granule interpretation is codec-defined, not container-defined"},
      {label:"bitstream_serial_number",k:"rsv",r:[14,17],val:"1 (u32 LE)",
        body:"Opaque 32-bit identifier for this logical bitstream, little-endian. Multiple logical streams multiplexed in one physical stream (e.g. video + audio) each carry a distinct serial number.",
        note:"0x01 0x00 0x00 0x00 reads as 1 little-endian; the value is opaque and chosen by the encoder"},
      {label:"page_sequence_number",k:"enum",r:[18,21],val:"0 (first page)",
        body:"Monotonically increasing page counter for this logical bitstream, little-endian. Starts at 0 on the BOS page. A gap in sequence numbers signals a dropped or corrupt page during streaming.",
        note:"used for loss detection and recovery; resets at BOS for each logical stream"},
      {label:"CRC_checksum",k:"rsv",r:[22,25],val:"0x114A27CE (example, LE)",
        body:"CRC-32 of the entire page (header + segment table + data) computed with this field zeroed. Same polynomial as Ethernet/PKZIP (0x04C11DB7), but unreflected with init 0 and xorout 0 -- so a stock reflected CRC-32 yields a different value; use the OGG-specific implementation.",
        note:"stored bytes 0xCE 0x27 0x4A 0x11 read little-endian as 0x114A27CE; a mismatch indicates a corrupt page"},
      {label:"page_segments",k:"enum",r:[26,26],val:"1 segment",
        body:"Count of lacing bytes in the segment table that immediately follows this 27-byte fixed header. Each lacing byte describes one segment of 0-255 data bytes.",
        note:"the segment table is page_segments bytes long; the sum of all lacing bytes equals the total data bytes carried on this page"}
    ]});
R.push({page:"ogg-anatomy.html",mount:"hdr-type",unit:"bit",bytes:[0x02],fields:[
    {label:"reserved (RFC bits 7-3)",k:"rsv",r:[0,4],val:"00000 (unused)",
      body:"Display bits 0-4 correspond to RFC 3533 bits 7-3 (mask 0xF8). All five are reserved; RFC 3533 requires them to be zero and readers must ignore them."},
    {label:"EOS -- end of stream",k:"flag",r:[5,5],val:"0 (clear)",
      body:"RFC bit 2 (mask 0x04), display bit 5. Set on the final page of a logical bitstream. A reader uses this to confirm a clean end without having to read further.",
      note:"clear here; absent from a BOS page -- both BOS and EOS set simultaneously is valid for a single-page stream"},
    {label:"BOS -- beginning of stream",k:"flag",r:[6,6],val:"1 (set)",
      body:"RFC bit 1 (mask 0x02), display bit 6. Marks the very first page of a logical bitstream. The payload of this page carries the codec identification header.",
      note:"set here; the codec ID header (Vorbis / Opus / FLAC-in-Ogg) is in this page's data segment"},
    {label:"continued packet",k:"flag",r:[7,7],val:"0 (clear)",
      body:"RFC bit 0 (mask 0x01), display bit 7. Set when the first segment on this page is the tail of a packet that started on a previous page (i.e. a packet split across a page boundary).",
      note:"clear here; the first segment on this page begins a new, complete packet"}
  ]});
R.push({page:"rmid-anatomy.html",mount:"block-head",unit:"byte",bytes:[0x52,0x49,0x46,0x46, 0x26,0x00,0x00,0x00, 0x52,0x4D,0x49,0x44],fields:[
      {label:"magic",k:"sync",r:[0,3],val:'\'"RIFF"\'',
       body:"The RIFF container signature. Little-endian framing, same as WAV; only the form type below distinguishes RMID from a WAV.",
       note:"52 49 46 46."},
      {label:"riff_size",k:"sync",r:[4,7],val:"38",
       body:"uint32 LITTLE-endian: bytes after this field. Note the endianness flip against the big-endian MIDI file it wraps.",
       note:"26 00 00 00 = 0x26 = 38."},
      {label:"form",k:"enum",r:[8,11],val:'\'"RMID"\'',sel:0,branch:[
        ["RMID","MIDI in RIFF"],["WAVE","a WAV, not this"]],
       body:"The RIFF form type. RMID means the data chunk holds a Standard MIDI File. RIFF....WAVE would be a WAV instead; the sniffer keys on exactly this.",
       note:"the whole point of the format: a MIDI dressed as a RIFF."}
    ]});
R.push({page:"rx2-anatomy.html",mount:"block-head",unit:"byte",bytes:[0x43,0x41,0x54,0x20, 0x00,0x00,0x08,0x00, 0x52,0x45,0x58,0x32],fields:[
      {label:"group",k:"sync",r:[0,3],val:'\'"CAT "\'',
       body:"The IFF group keyword (note the trailing space). Like AIFF\'s FORM, but the group is CAT and the form type below is REX2. All multi-byte fields are BIG-endian.",
       note:"43 41 54 20."},
      {label:"cat_size",k:"sync",r:[4,7],val:"2048",
       body:"uint32 BIG-endian: bytes in the group after this field.",
       note:"00 00 08 00 = 0x0800 = 2048."},
      {label:"form",k:"enum",r:[8,11],val:'\'"REX2"\'',
       body:"The form type. REX2 marks a ReCycle loop; the child chunks (HEAD, CREI, GLOB, SINF, SDAT) follow, with SLCE nested inside a CAT/SLCL sub-group rather than at this level.",
       note:"the slice markers live in a nested CAT/SLCL sub-group."}
    ]});
R.push({page:"serum-anatomy.html",mount:"serum-hdr",unit:"byte",bytes:[0x58,0x66,0x65,0x72,0x4A,0x73,0x6F,0x6E, 0x00, 0x4F,0x01,0x00,0x00, 0x00,0x00,0x00,0x00],fields:[
        {label:"magic",k:"sync",r:[0,7],val:"\"XferJson\"",body:"The 8-byte signature shared by Serum presets, wavetables and FXP wrappers. acidcat sniffs it to route the file, then finds the metadata by the first brace.",note:"ASCII \"XferJson\"."},
        {label:"flag",k:"rsv",r:[8,8],val:"0x00",body:"A single preamble byte, 0 in this preset. Not interpreted by the walker."},
        {label:"json_length",k:"enum",r:[9,12],val:"335",body:"little-endian u32: the byte length of the JSON block that follows. A cross-check -- acidcat still locates the JSON by scanning for '{', so a resized preamble does not break it.",note:"0x0000014F = 335, exactly the JSON size."},
        {label:"reserved",k:"rsv",r:[13,16],val:"0",body:"A reserved u32, 0 here. The JSON's opening brace begins at the next byte (offset 17)."}
      ]});
R.push({page:"serum-anatomy.html",mount:"serum-json",unit:"byte",bytes:[0x7B,0x22,0x66,0x69,0x6C,0x65,0x54,0x79,0x70,0x65,0x22,0x3A,0x22,0x53,0x65,0x72,0x75,0x6D,0x50,0x72,0x65,0x73,0x65,0x74],fields:[
        {label:"brace",k:"sync",r:[0,0],val:"{",body:"The opening brace of the JSON object. This is the byte acidcat scans for to locate the metadata -- endianness-free, version-proof."},
        {label:"key",k:"enum",r:[1,10],val:"\"fileType\"",body:"The first key, as quoted ASCII. JSON keys and values are plain UTF-8 text -- no length prefixes, no binary framing."},
        {label:"colon",k:"rsv",r:[11,11],val:":",body:"JSON key/value separator."},
        {label:"value",k:"flag",r:[12,23],val:"\"SerumPreset\"",body:"The value for fileType: the quoted string \"SerumPreset\". The block continues with presetName, presetAuthor, tags, product, and the rest -- all readable text.",note:"the full block is 335 bytes; this is its first 24."}
      ]});
R.push({page:"serum-anatomy.html",mount:"serum-blob",unit:"byte",bytes:[0x5C,0xA9,0x01,0x00, 0x02,0x00,0x00,0x00, 0x28,0xB5,0x2F,0xFD, 0xA0,0x5C,0xA9,0x01],fields:[
        {label:"uncompressed_size",k:"enum",r:[0,3],val:"108,892",body:"little-endian u32: the size of the blob once decompressed. A hint the host uses to size its buffer before inflating the zstd frame.",note:"0x0001A95C = 108892 bytes."},
        {label:"count",k:"rsv",r:[4,7],val:"2",body:"little-endian u32, a small count/version word in the blob preamble (2 here). Not decoded -- part of the opaque interior framing."},
        {label:"zstd_magic",k:"flag",r:[8,11],val:"0xFD2FB528",sel:0,branch:[["28 B5 2F FD","Zstandard frame (Serum 2)"],["(absent)","raw blob (Serum 1)"]],
          body:"The Zstandard magic number, little-endian 0xFD2FB528. Its presence marks a Serum 2 compressed blob; Serum 1 wrote the wavetable/mod data uncompressed, with no frame here.",
          note:"a reliable Serum 1 vs 2 tell, independent of the product string."},
        {label:"frame_header",k:"flag",r:[12,15],val:"zstd frame header",body:"The start of the zstd frame header -- byte 12 (0xA0) is the frame-header descriptor, encoding the window size and whether a content-size field follows. acidcat does not decompress past here.",note:"the interior wavetable/mod format is undocumented."}
      ]});
R.push({page:"sigmf-anatomy.html",mount:"sigmf-meta",unit:"byte",bytes:[0x7B, 0x22,0x67,0x6C,0x6F,0x62,0x61,0x6C,0x22, 0x3A, 0x7B, 0x22,0x63,0x6F,0x72,0x65,0x3A,0x64,0x61,0x74,0x61,0x74,0x79,0x70,0x65,0x22, 0x3A],fields:[
        {label:"open",k:"sync",r:[0,0],val:"{",body:"The opening brace of the meta JSON object. The sidecar is one object with global / captures / annotations."},
        {label:"global_key",k:"enum",r:[1,8],val:"\"global\"",body:"The first top-level key. The global object holds the fields that make the raw data readable -- datatype, sample_rate, version, sha512, hardware."},
        {label:"colon",k:"rsv",r:[9,9],val:":",body:"JSON key/value separator."},
        {label:"global_obj",k:"sync",r:[10,10],val:"{",body:"The brace opening the global object's own key/value pairs."},
        {label:"datatype_key",k:"enum",r:[11,25],val:"\"core:datatype\"",body:"The core:datatype key -- the single most important field. Its value decides how every byte of the .sigmf-data stream is read. All SigMF keys live under the core: namespace.",note:"acidcat parses its value into a sample geometry before touching the data."},
        {label:"colon2",k:"rsv",r:[26,26],val:":",body:"Separator before the datatype value (\"ci16_le\", broken down on the Datatype tab)."}
      ]});
R.push({page:"sigmf-anatomy.html",mount:"sigmf-dtype",unit:"byte",bytes:[0x63, 0x69, 0x31,0x36, 0x5F,0x6C,0x65],fields:[
        {label:"complexity",k:"enum",r:[0,0],val:"c = complex",sel:0,branch:[["c","complex (I/Q pairs)"],["r","real"]],
          body:"The first letter: c = complex (each sample is an interleaved I/Q pair), r = real (one component). c here, so the stream reads I Q I Q ...",
          note:"a leading r or c; omitted historically defaulted to real."},
        {label:"numeric",k:"enum",r:[1,1],val:"i = signed int",sel:0,branch:[["i","signed integer"],["u","unsigned integer"],["f","float"]],
          body:"The numeric type: i = signed int, u = unsigned int, f = IEEE float. i here.",
          note:"determines how a component's bits are interpreted."},
        {label:"bits",k:"enum",r:[2,3],val:"16",body:"The bit width of each component: 8, 16, 32, or 64. 16 here -- so each I or Q is an int16.",note:"complex int16 -> 4 bytes per sample."},
        {label:"endian",k:"flag",r:[4,6],val:"_le",sel:0,branch:[["_le","little-endian"],["_be","big-endian"]],
          body:"The byte-order suffix, required for any width above 8 bits. _le = little-endian. acidcat rejects a multibyte datatype that omits it -- the spec mandates the suffix.",note:"an 8-bit type needs no suffix (one byte has no order)."}
      ]});
R.push({page:"sigmf-anatomy.html",mount:"sigmf-data",unit:"byte",bytes:[0xFC,0xFF, 0x02,0x00, 0xFD,0xFF, 0x03,0x00],fields:[
        {label:"I[0]",k:"enum",r:[0,1],val:"-4",body:"Sample 0, in-phase component: a little-endian int16 under the ci16_le geometry. 0xFFFC = -4.",note:"the real part of the first complex sample."},
        {label:"Q[0]",k:"flag",r:[2,3],val:"2",body:"Sample 0, quadrature component: little-endian int16. 0x0002 = 2. Together with I[0] this is the complex sample (-4, 2).",note:"the imaginary part."},
        {label:"I[1]",k:"enum",r:[4,5],val:"-3",body:"Sample 1, in-phase. 0xFFFD = -3. The stream just continues -- no separator, no framing.",note:"I and Q interleave exactly like L and R in a stereo WAV."},
        {label:"Q[1]",k:"flag",r:[6,7],val:"3",body:"Sample 1, quadrature. 0x0003 = 3. Complex sample 1 = (-3, 3). Nothing here says these are int16 -- only the meta's datatype does.",note:"lose the meta and the geometry is a guess."}
      ]});
R.push({page:"wav-anatomy.html",mount:"block-fmt",unit:"byte",bytes:[0x01,0x00, 0x02,0x00, 0x44,0xAC,0x00,0x00, 0x10,0xB1,0x02,0x00, 0x04,0x00, 0x10,0x00],fields:[
        {label:"format_tag",k:"enum",r:[0,1],val:"PCM (0x0001)",sel:0,branch:[
          ["0x0001","PCM"],["0x0002","MS ADPCM"],["0x0003","IEEE float"],["0x0006","A-law"],["0x0007","mu-law"],
          ["0x0011","IMA ADPCM"],["0x0055","MPEG layer III"],["0xFFFE","extensible (GUID)"]],
          body:"The codec. PCM here. 0xFFFE extends the chunk with a sub-format GUID.",
          note:"acidcat reads tag, channels, rate, bits; non-PCM uses the fact chunk for sample count."},
        {label:"channels",k:"enum",r:[2,3],val:"2",body:"Channel count. Samples are interleaved L,R,L,R."},
        {label:"sample_rate",k:"enum",r:[4,7],val:"44100 Hz",body:"Frames per second, u32 little-endian. 0xAC44 = 44100."},
        {label:"avg_bytes_per_sec",k:"enum",r:[8,11],val:"176400",body:"sample_rate * block_align. A decoder hint; acidcat lints it against the computed value.",note:"44100 * 4 = 176400."},
        {label:"block_align",k:"enum",r:[12,13],val:"4",body:"Bytes per sample frame = channels * bits/8. One L+R pair is 4 bytes here."},
        {label:"bits_per_sample",k:"enum",r:[14,15],val:"16",body:"Bit depth per sample. 8 unsigned, 16/24/32 signed LE, or 32/64 float when tag is 3."}
      ]});
R.push({page:"wav-anatomy.html",mount:"block-acid",unit:"byte",bytes:[0x02,0x00,0x00,0x00, 0x3C,0x00, 0x00,0x80, 0x00,0x00,0x00,0x00, 0x08,0x00,0x00,0x00, 0x04,0x00, 0x04,0x00, 0x00,0x00,0xF0,0x42],fields:[
        {label:"type_flags",k:"flag",r:[0,3],val:"root set (0x02)",sel:1,branch:[
          ["0x01","one-shot (not a loop)"],["0x02","root note valid"],["0x04","tempo stretch"],["0x08","disk-based"]],
          body:"Bitmask. 0x02 here: this is a loop with a valid root note. The one-shot bit, when set, is unreliable in the wild.",
          note:"acidcat trusts num_beats only when one-shot is clear, or when beats reconcile with duration."},
        {label:"root_note",k:"enum",r:[4,5],val:"60 = C4",body:"MIDI root note of the loop. 60 is middle C. 0 is treated as unset."},
        {label:"unknown1",k:"rsv",r:[6,7],val:"0x8000",body:"Reserved/observed constant. Not interpreted."},
        {label:"unknown2",k:"rsv",r:[8,11],val:"0.0 (f32)",body:"Reserved float, observed 0.0 across vendors."},
        {label:"num_beats",k:"enum",r:[12,15],val:"8",body:"Beat count of the loop. With tempo gives expected duration: beats / tempo * 60.",note:"the field sits at 0x0C, after the unknown float; reading beats at 0x08 yields 0."},
        {label:"meter_denominator",k:"enum",r:[16,17],val:"4",body:"Lower number of the time signature (the 4 in 4/4)."},
        {label:"meter_numerator",k:"enum",r:[18,19],val:"4",body:"Upper number of the time signature."},
        {label:"tempo",k:"enum",r:[20,23],val:"120.0 BPM",body:"Original tempo as an IEEE-754 float. 0x42F00000 = 120.0.",note:"acidcat warns if tempo is outside 40-300 or disagrees with beats and duration."}
      ]});
R.push({page:"wav-anatomy.html",mount:"block-smpl",unit:"byte",bytes:[0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0xE8,0x03,0x00,0x00, 0x2B,0xB0,0x00,0x00, 0x00,0x00,0x00,0x80, 0x00,0x00,0x00,0x00],fields:[
        {label:"identifier",k:"enum",r:[0,3],val:"0",body:"Loop id. May match a cue point's dwName so an editor can label the loop; 0 is a fine and common handle."},
        {label:"type",k:"enum",r:[4,7],val:"forward (0)",sel:0,branch:[
          ["0","forward"],["1","alternating (ping-pong)"],["2","backward"],["3-31","reserved"],["32+","vendor-defined"]],
          body:"Playback direction. Forward dominates the wild; ping-pong reverses at each end for a smoother sustain.",
          note:"values above 31 are vendor extensions and rarely honored."},
        {label:"start",k:"enum",r:[8,11],val:"1000",body:"Loop start, in sample frames, u32 LE. 0x000003E8 = 1000."},
        {label:"end",k:"enum",r:[12,15],val:"45099 (inclusive)",body:"Loop end, in sample frames -- and this sample ALSO plays. Length = end - start + 1 = 44100 frames = 1.000 s at 44.1 kHz.",note:"the inclusive end is the classic interop bug: a reader that assumes exclusive drops the final frame and the loop clicks. 0x0000B02B = 45099."},
        {label:"fraction",k:"enum",r:[16,19],val:"0x80000000 = 1/2 frame",body:"Sub-sample loop tuning for fine pitch: a fraction of one sample frame. 0x80000000 is half a frame.",note:"almost no reader honors it; most treat the loop as sample-aligned."},
        {label:"play_count",k:"enum",r:[20,23],val:"0 = infinite",body:"Times to play the loop before moving on. 0 means sustain forever, the usual value for an instrument loop."}
      ]});
R.push({page:"wav-anatomy.html",mount:"riff-hdr",unit:"byte",bytes:[0x52,0x49,0x46,0x46, 0x24,0x08,0x00,0x00, 0x57,0x41,0x56,0x45],fields:[
        {label:"magic",k:"sync",r:[0,3],val:"\"RIFF\"",sel:0,branch:[["RIFF","little-endian"],["RIFX","big-endian"],["RF64","64-bit (see RF64 tab)"]],
          body:"The container signature. \"RIFF\" is little-endian; \"RIFX\" flips every multi-byte value to big-endian; \"RF64\" signals the large-file variant.",
          note:"acidcat sniffs the first 4 bytes, then requires the form type at 0x08 to be WAVE."},
        {label:"riff_size",k:"enum",r:[4,7],val:"2084",body:"u32 LE = total file size minus 8 (the 8 bytes of magic + this field). 0x0824 = 2084.",
          note:"routinely left stale by writers; acidcat lints it against the real length and never trusts the claim."},
        {label:"form_type",k:"sync",r:[8,11],val:"\"WAVE\"",body:"The RIFF form. WAVE for audio; AVI, RMID, RDIB, sfbk (SoundFont), DLS are other forms a reader branches on here."}
      ]});
R.push({page:"wav-anatomy.html",mount:"fmt-ext",unit:"byte",bytes:[0xFE,0xFF, 0x02,0x00, 0x80,0xBB,0x00,0x00, 0x00,0xDC,0x05,0x00, 0x08,0x00, 0x20,0x00, 0x16,0x00, 0x18,0x00, 0x03,0x00,0x00,0x00, 0x01,0x00,0x00,0x00,0x00,0x00,0x10,0x00,0x80,0x00,0x00,0xAA,0x00,0x38,0x9B,0x71],fields:[
        {label:"format_tag",k:"enum",r:[0,1],val:"EXTENSIBLE (0xFFFE)",body:"Signals the extended 40-byte descriptor; the real codec lives in the sub_format GUID at the end, not here."},
        {label:"channels",k:"enum",r:[2,3],val:"2",body:"Channel count. Which physical speaker each one drives is named by the channel_mask."},
        {label:"sample_rate",k:"enum",r:[4,7],val:"48000 Hz",body:"Frames per second, u32 LE. 0x0000BB80 = 48000."},
        {label:"avg_bytes_per_sec",k:"enum",r:[8,11],val:"384000",body:"sample_rate * block_align = 48000 * 8.",note:"0x0005DC00 = 384000."},
        {label:"block_align",k:"enum",r:[12,13],val:"8",body:"Bytes per frame = channels * container_bits/8 = 2 * 32/8 = 8. Note it uses the container size, not valid_bits."},
        {label:"bits_per_sample",k:"enum",r:[14,15],val:"32 (container)",body:"The container word size, 32 here. The REAL precision is in valid_bits below -- this is 24-bit audio in a 32-bit word."},
        {label:"cb_size",k:"enum",r:[16,17],val:"22",body:"Extension byte count after this field. 22 = WAVEFORMATEXTENSIBLE (valid_bits + mask + 16-byte GUID)."},
        {label:"valid_bits_per_sample",k:"enum",r:[18,19],val:"24",body:"The real bit depth inside the 32-bit container word. Makes 24-in-32 padding explicit instead of guessed."},
        {label:"channel_mask",k:"flag",r:[20,23],val:"FL FR (0x3)",sel:0,branch:[
          ["0x1","FL front left"],["0x2","FR front right"],["0x4","FC center"],["0x8","LFE"],["0x10","BL back left"],["0x20","BR back right"]],
          body:"Speaker bitmask, low bit first: FL FR FC LFE BL BR FLC FRC BC SL SR ... 0x3 = front L + R (plain stereo).",
          note:"the order of the samples follows the mask bit order, not the channel index."},
        {label:"sub_format",k:"sync",r:[24,39],val:"PCM GUID",body:"16-byte GUID: the leading 2 bytes are the real format tag (0x0001 = PCM) and the fixed 14-byte tail 00 00 00 00 10 00 80 00 00 AA 00 38 9B 71 marks it a standard KSDATAFORMAT_SUBTYPE.",note:"a non-standard tail (an unofficial codec GUID) is warned by acidcat."}
      ]});
R.push({page:"wav-anatomy.html",mount:"ds64-map",unit:"byte",bytes:[0xA0,0xCB,0x4C,0x00,0x01,0x00,0x00,0x00, 0x00,0xCB,0x4C,0x00,0x01,0x00,0x00,0x00, 0xC0,0x32,0x13,0x40,0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00],fields:[
        {label:"riff_size",k:"sync",r:[0,7],val:"4,300,000,160",body:"The true size of the whole RF64 chunk as a u64 LE, standing in for the 32-bit RIFF size field which now holds the 0xFFFFFFFF sentinel.",note:"0x1004CCBA0 = 4,300,000,160 bytes."},
        {label:"data_size",k:"enum",r:[8,15],val:"4,300,000,000",body:"The true size of the data chunk, u64 LE -- 4.3 GB, past the 4 GB (0xFFFFFFFF) u32 ceiling. This is the whole reason RF64 exists.",note:"0x1004CCB00 = 4,300,000,000."},
        {label:"sample_count",k:"enum",r:[16,23],val:"1,075,000,000",body:"Samples per channel, u64 LE -- the fact-chunk value at 64-bit. 4,300,000,000 bytes / 4 bytes-per-frame.",note:"0x401332C0 = 1,075,000,000."},
        {label:"table_length",k:"enum",r:[24,27],val:"0",body:"Number of chunk-size override records that follow (each a 4-byte id + u64 size), for any OTHER chunk also over 4 GB. 0 here -- only data is large."}
      ]});
R.push({page:"wav-anatomy.html",mount:"wave64-map",unit:"byte",bytes:[0x72,0x69,0x66,0x66,0x2E,0x91,0xCF,0x11,0xA5,0xD6,0x28,0xDB,0x04,0xC1,0x00,0x00, 0x00,0x00,0x10,0x00,0x00,0x00,0x00,0x00, 0x77,0x61,0x76,0x65,0xF3,0xAC,0xD3,0x11,0x8C,0xD1,0x00,0xC0,0x4F,0x8E,0xDB,0x8A],fields:[
        {label:"riff_guid",k:"sync",r:[0,15],val:"RIFF GUID",sel:0,branch:[["72 69 66 66","= 'riff' in ASCII, LE"],["2E 91 CF 11","v1-UUID layout; time_low forced to ASCII"],["A5 D6 28 DB 04 C1 00 00","node + clock-seq (MS DirectShow-style)"]],
          body:"The 16-byte container id, replacing the 4-character \"RIFF\". Its first four bytes literally spell 'riff'; the rest follows a version-1 UUID layout, but its time_low field is overwritten to spell 'riff', so no exact timestamp remains.",
          note:"the node bytes resemble MS DirectShow-era GUID constants (identifiers, not an encoded creation time); Sonic Foundry's CTO was ex-Microsoft."},
        {label:"file_size",k:"enum",r:[16,23],val:"1,048,576",body:"Total file size as a u64 LE. Unlike RIFF, Wave64 sizes INCLUDE the 24-byte chunk header (16-byte GUID + 8-byte size), not just the payload.",note:"0x100000 = 1,048,576 (1 MB, an example)."},
        {label:"wave_guid",k:"sync",r:[24,39],val:"WAVE GUID",sel:0,branch:[["77 61 76 65","= 'wave' in ASCII, LE"],["F3 AC D3 11","v1-UUID layout; time_low forced to ASCII"],["8C D1 00 C0 4F 8E DB 8A","node + clock-seq"]],
          body:"The 16-byte form-type id, the analogue of \"WAVE\". Its first four bytes spell 'wave'; its time_low is likewise overwritten to spell 'wave', so like the container GUID it carries no exact timestamp."}
      ]});
R.push({page:"wav-anatomy.html",mount:"aiff-comm",unit:"byte",bytes:[0x00,0x02, 0x00,0x01,0x58,0x88, 0x00,0x10, 0x40,0x0E,0xAC,0x44,0x00,0x00,0x00,0x00,0x00,0x00],fields:[
        {label:"num_channels",k:"enum",r:[0,1],val:"2",body:"Channel count, i16 BIG-endian (note the byte order flip from WAV). Samples in SSND are interleaved by channel."},
        {label:"num_sample_frames",k:"enum",r:[2,5],val:"88200",body:"Frames per channel, u32 big-endian. duration = frames / sample_rate.",note:"this is the authoritative length -- unlike WAV, where you divide the data-chunk size by the frame size."},
        {label:"bits_per_sample",k:"enum",r:[6,7],val:"16",body:"Bit depth per sample, i16 big-endian."},
        {label:"sample_rate",k:"enum",r:[8,17],val:"44100 Hz",sel:3,branch:[
          ["sign","1 bit"],["exponent","15 bits, bias 16383"],["mantissa","64 bits, explicit leading 1"],
          ["value","2^(exp-16383) * mantissa / 2^63"]],
          body:"An 80-bit IEEE-754 extended-precision float, a 68k-era Apple format. Python's struct has no code for it, so acidcat reassembles it by hand from the exponent and the explicit 64-bit mantissa.",
          note:"40 0E AC 44 00.. : exponent 0x400E = 16398 = 16383 + 15; mantissa 0xAC44 / 2^15 = 1.3458.., times 2^15 = 44100. an explicit leading mantissa bit, unlike float64. an all-ones exponent (IEEE inf/NaN) decodes to 0 = unset, never a rate."}
      ]});
R.push({page:"wt-anatomy.html",mount:"block-head",unit:"byte",bytes:[0x76,0x61,0x77,0x74, 0x00,0x08,0x00,0x00, 0x00,0x01, 0x0C,0x00],fields:[
      {label:"magic",k:"sync",r:[0,3],val:'"vawt"',
       body:"The four-byte signature at offset 0. This is its own container, not a RIFF chunk, so vawt sits at the very start of the file.",
       note:"do not confuse with the BWBM beat-map chunk Bitwig writes inside WAV files."},
      {label:"frame_samples",k:"enum",r:[4,7],val:"2048",
       body:"uint32 little-endian: the number of samples in one single-cycle wave. This is the wave length, not the total. Observed values are 256, 1024, and 2048.",
       note:"little-endian: 00 08 00 00 = 0x00000800 = 2048."},
      {label:"frame_count",k:"enum",r:[8,9],val:"256",
       body:"uint16 little-endian: how many single-cycle waves are stacked in the table. 1 for a single waveform, up to 256 and beyond for a table the oscillator sweeps through.",
       note:"00 01 = 0x0100 = 256. a WAV dropped in becomes as many waves as its length divides into."},
      {label:"data_offset",k:"sync",r:[10,11],val:"12",
       body:"uint16 little-endian: byte offset where the sample data begins. Always 12, the header length; the samples follow immediately with no gap.",
       note:"0C 00 = 12."}
    ]});

var out=[];
R.forEach(function(r){
  var N = r.unit==="bit" ? r.bytes.length*8 : r.bytes.length;
  var owned=new Array(N), probs=[];
  r.fields.forEach(function(f,fi){
    if(!f.r){probs.push("field "+fi+" ("+(f.label||"?")+") has no range");return;}
    if(f.r[1]>=N) probs.push("'"+(f.label||fi)+"' range ["+f.r+"] past end ("+N+")");
    for(var q=f.r[0];q<=f.r[1] && q<N;q++){
      if(owned[q]!==undefined)
        probs.push("unit "+q+" claimed by '"+(r.fields[owned[q]].label||owned[q])+"' and '"+(f.label||fi)+"'");
      owned[q]=fi;
    }
  });
  var gaps=[];
  for(var q=0;q<N;q++) if(owned[q]===undefined) gaps.push(q);
  if(gaps.length) probs.push("unclaimed units: "+(gaps.length>12?gaps.slice(0,12)+" ...("+gaps.length+")":gaps));
  if(probs.length) out.push({page:r.page,mount:r.mount,unit:r.unit,n:N,probs:probs});
});
if(!out.length) console.log("ALL CLEAN");
out.forEach(function(o){
  console.log("\n"+o.page+"  ["+o.mount+"]  "+o.n+" "+(o.unit==="bit"?"bits":"bytes"));
  o.probs.forEach(function(p){console.log("    "+p);});
});
console.log("\n"+out.length+" of "+R.length+" maps have problems");
