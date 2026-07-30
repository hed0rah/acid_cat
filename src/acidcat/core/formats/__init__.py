"""Per-format parsers -- read one file format's byte structure and metadata.

One module per format: containers (riff, aiff, flac, mp3, mp4, ogg), MIDI
(midi, ump), trackers (tracker), the Amiga voice format (svx), synth/sampler
presets (ni, serum, vital, bitwig, sf2), and cover-art (cover). These parse and
expose a format's fields; the walk/ package builds the structural-dump walkers
on top, the codecs decode its sample data, and write/ edits it.
"""
