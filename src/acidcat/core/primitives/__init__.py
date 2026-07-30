"""Shared low-level primitives.

The small, format-agnostic building blocks that were previously hand-rolled in
many modules -- signal/entropy measures, ADPCM helpers, chunk iteration, WAV
emit. Keeping one copy of each removes a large class of drift (a fix to the byte
math lands once) and makes the codecs/forensics layers thinner. Pure-stdlib and
dependency-free: everything above can import these; these import nothing of ours.
"""
