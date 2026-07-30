"""
Audio feature extraction for ML analysis.

Extracts 50+ spectral, rhythmic, and timbral features from audio files
using librosa.
"""

# canonical similarity vector: the ordered subset of the extracted features
# that describes *timbre and rhythm*, used for nearest-neighbour search. The
# raw dict also holds sample_rate, audio_length_samples, duration_sec, and
# beat_count -- deliberately EXCLUDED here: they carry file/scale information,
# not sonic character, and (being 10^4-10^6 in magnitude) would dominate a
# cosine over the small-magnitude timbral dims and collapse every result into
# one indistinguishable cluster. Bump FEATURE_SET_VERSION if this list changes,
# so stale vectors can be detected and re-derived.
FEATURE_KEYS = (
    "spectral_centroid_mean", "spectral_centroid_std",
    "spectral_rolloff_mean", "spectral_rolloff_std",
    "spectral_bandwidth_mean", "spectral_bandwidth_std",
    "zcr_mean", "zcr_std",
    "mfcc_1_mean", "mfcc_1_std", "mfcc_2_mean", "mfcc_2_std",
    "mfcc_3_mean", "mfcc_3_std", "mfcc_4_mean", "mfcc_4_std",
    "mfcc_5_mean", "mfcc_5_std", "mfcc_6_mean", "mfcc_6_std",
    "mfcc_7_mean", "mfcc_7_std", "mfcc_8_mean", "mfcc_8_std",
    "mfcc_9_mean", "mfcc_9_std", "mfcc_10_mean", "mfcc_10_std",
    "mfcc_11_mean", "mfcc_11_std", "mfcc_12_mean", "mfcc_12_std",
    "mfcc_13_mean", "mfcc_13_std",
    "chroma_mean", "chroma_std",
    "mel_mean", "mel_std",
    "tempo_librosa",
    "rms_mean", "rms_std",
    "spectral_contrast_mean", "spectral_contrast_std",
    "tonnetz_mean", "tonnetz_std",
)

FEATURE_SET_VERSION = 3   # 1 = pre-vector JSON only; 2 = adds the FEATURE_KEYS
                          # vector (native-rate analysis); 3 = analysis resampled
                          # to 22050 Hz (values differ from 2 -> not comparable).

# feature extraction is done at this fixed rate regardless of the file's rate
# (see FEATURE_SET_VERSION note): the MIR standard, and it decouples timbre
# comparison from source sample rate.
ANALYSIS_SR = 22050

FEATURE_DIMS = len(FEATURE_KEYS)


def vector_from_features(feats):
    """Project an extracted-features dict onto the canonical FEATURE_KEYS order,
    returning a plain list of floats (stdlib only; no numpy). Missing/non-finite
    values become 0.0. Returns None if `feats` is falsy."""
    if not feats:
        return None
    out = []
    for k in FEATURE_KEYS:
        v = feats.get(k)
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        if v != v or v in (float("inf"), float("-inf")):   # NaN / inf guard
            v = 0.0
        out.append(v)
    return out


def extract_audio_features(filepath):
    """
    Extract audio features for ML analysis.

    Returns dict with 50+ features (spectral, timbral, rhythmic),
    or None if the file is too short or unreadable.
    """
    import warnings
    warnings.filterwarnings("ignore")

    try:
        import librosa
        import numpy as np
    except ImportError:
        from acidcat.util.deps import require
        require("librosa", "numpy", group="analysis")
        return None

    try:
        # Analyze at a fixed 22050 Hz (the MIR standard: Nyquist 11025 covers all
        # musically relevant content). Every transform below scales with the sample
        # count, so resampling from 44.1k/96k is a big speedup at no cost to the
        # timbral features -- but it changes their VALUES, which is why this is
        # gated behind FEATURE_SET_VERSION (find_similar only compares same-version
        # vectors, so old native-rate vectors are ignored, not silently mixed).
        sr_native = librosa.get_samplerate(filepath)      # header read, for reporting
        y, sr = librosa.load(filepath, sr=ANALYSIS_SR, mono=True)

        if len(y) < 256:
            return None

        features = {}

        # Basic properties -- report the file's TRUE rate/length, not the analysis
        # rate, so the metadata view is honest about the source.
        features['duration_sec'] = len(y) / sr
        features['sample_rate'] = sr_native
        features['audio_length_samples'] = int(round(features['duration_sec'] * sr_native))

        # One magnitude STFT, shared by the spectral features that use it. Each of
        # these recomputed its own identical STFT before; feeding S= is
        # bit-identical (verified) and drops the redundant transforms.
        S = np.abs(librosa.stft(y))

        # Spectral features (from the shared STFT)
        spectral_centroids = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
        features['spectral_centroid_mean'] = np.mean(spectral_centroids)
        features['spectral_centroid_std'] = np.std(spectral_centroids)

        spectral_rolloff = librosa.feature.spectral_rolloff(S=S, sr=sr)[0]
        features['spectral_rolloff_mean'] = np.mean(spectral_rolloff)
        features['spectral_rolloff_std'] = np.std(spectral_rolloff)

        spectral_bandwidth = librosa.feature.spectral_bandwidth(S=S, sr=sr)[0]
        features['spectral_bandwidth_mean'] = np.mean(spectral_bandwidth)
        features['spectral_bandwidth_std'] = np.std(spectral_bandwidth)

        # Zero crossing rate
        zcr = librosa.feature.zero_crossing_rate(y)[0]
        features['zcr_mean'] = np.mean(zcr)
        features['zcr_std'] = np.std(zcr)

        # MFCC features (first 13 coefficients)
        mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        for i in range(13):
            features[f'mfcc_{i+1}_mean'] = np.mean(mfccs[i])
            features[f'mfcc_{i+1}_std'] = np.std(mfccs[i])

        # Chroma features
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        features['chroma_mean'] = np.mean(chroma)
        features['chroma_std'] = np.std(chroma)

        # Mel-frequency features
        mel_spectrogram = librosa.feature.melspectrogram(y=y, sr=sr)
        features['mel_mean'] = np.mean(mel_spectrogram)
        features['mel_std'] = np.std(mel_spectrogram)

        # Tempo. Use the tempo estimator directly rather than full beat tracking:
        # beat_track adds a ~1.5s dynamic-programming beat search whose beat
        # positions we discard, and its tempo is bit-identical to this. This is
        # the single biggest cost in the extractor (~75% of per-file time).
        tempo = librosa.feature.tempo(y=y, sr=sr)
        features['tempo_librosa'] = float(np.atleast_1d(tempo)[0])
        # beat_count is display-only (not in the similarity vector); estimate it
        # from tempo x duration instead of paying for beat positions we discard.
        features['beat_count'] = int(round(
            features['tempo_librosa'] * features['duration_sec'] / 60.0))

        # RMS energy (kept time-domain: rms(S=) differs slightly from rms(y=))
        rms = librosa.feature.rms(y=y)[0]
        features['rms_mean'] = np.mean(rms)
        features['rms_std'] = np.std(rms)

        # Spectral contrast (from the shared STFT)
        contrast = librosa.feature.spectral_contrast(S=S, sr=sr)
        features['spectral_contrast_mean'] = np.mean(contrast)
        features['spectral_contrast_std'] = np.std(contrast)

        # Tonnetz (tonal centroid features)
        tonnetz = librosa.feature.tonnetz(y=y, sr=sr)
        features['tonnetz_mean'] = np.mean(tonnetz)
        features['tonnetz_std'] = np.std(tonnetz)

        return features

    except Exception as e:
        return None
