"""Audio-content analysis -- the sound-understanding hat.

Derive musical/timbral features from decoded audio: BPM and key detection
(detect), the ML feature vector (features), and Camelot-wheel key utilities
(camelot). These lean on librosa/numpy, loaded only when an analysis command
runs; the rest of acidcat needs none of it.
"""
