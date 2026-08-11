"""Extract -- pull embedded audio out of a container, as playable samples.

The unified sample-extraction orchestrator (samples) walks any bank/module
acidcat can parse and yields ready-to-write WAVs; the ROM rippers (n64rip,
snesrip) recover VADPCM / BRR samples straight from cartridge images by
statistical coherence. The read side of write/: these decode and emit, never
mutating the source.
"""
