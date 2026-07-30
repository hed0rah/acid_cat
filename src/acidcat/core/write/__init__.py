"""Write, edit, and repair -- mutate a file and re-emit valid bytes.

One idea underneath: a file is a set of structural constraints (structure.py,
constraints.py), so editing metadata (edits + the edit_riff/edit_aiff emitters),
repairing a broken container (repairers, countrepair, flacrepair, mp4repair),
and validating are the same operation -- mutate the tree, re-satisfy the
constraints, re-serialize. midi_write emits Standard MIDI Files.
"""
