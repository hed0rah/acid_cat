"""Audio codecs -- decode compressed/encoded sample bytes to linear PCM.

Each module turns a format's coded samples (ADPCM variants, DSP, BRR, VADPCM,
NCW, ...) into 16-bit PCM. Codecs depend only on core/primitives and stdlib;
the walkers and the convert/extract commands drive them.
"""
