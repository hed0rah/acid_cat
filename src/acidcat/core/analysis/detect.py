"""
BPM and key detection.

Combines multiple strategies: RIFF chunk metadata, filename parsing,
and librosa audio analysis with smart validation/fallback.
"""

import os
import re

# ── Filename parsing ───────────────────────────────────────────────

def parse_bpm_from_filename(filepath):
    """Extract BPM from filename using common patterns. Returns int or None."""
    filename = os.path.basename(filepath)
    bpm_patterns = [
        r'(\d{2,3})\s*bpm',
        r'bpm\s*(\d{2,3})',
        r'(\d{2,3})bpm',
        # bare 2-3 digit run, not adjacent to digits, decimals, OR letters.
        # Letter-adjacent rejection prevents pack identifiers like '91V_SBH'
        # from matching as BPM 91; the parser falls through to the real
        # tempo marker (e.g. _126_ later in the filename). Zero-width
        # lookarounds so consecutive numbers (e.g. '_03_126_') both surface
        # instead of the first consuming the shared underscore.
        r'(?<![\d.A-Za-z])(\d{2,3})(?![\d.A-Za-z])',
    ]
    # iterate ALL matches of each pattern; a filename like "Pack_03_126_A#"
    # matches "_03_" before "_126_" so we need to consider every occurrence.
    for pattern in bpm_patterns:
        for match in re.findall(pattern, filename, re.IGNORECASE):
            bpm = int(match)
            # 60..300 covers everything from slow ballad to gabber.
            # DnB at 174, hardcore at 220, gabber at 240 all pass.
            if 60 <= bpm <= 300:
                return bpm
    return None


# flat -> sharp normalization for consistency with MIDI note naming.
# Cb/Fb aren't pitch-raising flats; they're enharmonic with B/E.
_FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#",
                  "Bb": "A#", "Cb": "B", "Fb": "E"}


def _normalize_root(note):
    """'eb'/'EB'/'Eb' -> 'D#'; 'f#' -> 'F#'; 'a' -> 'A'."""
    root = note[0].upper() + note[1:].lower()
    return _FLAT_TO_SHARP.get(root, root)


def parse_key_from_filename(filepath):
    """Extract musical key from filename. Returns string like 'C#m' or None.

    Flats are accepted and normalized to sharps ('Eb minor' -> 'D#m').
    A bare capital-M suffix is the major marker used by Beatport /
    Mixed In Key / Serato / Rekordbox ('F#M' -> F# major); lowercase
    'm' is minor. The worded suffixes (maj/major/min/minor) stay
    case-insensitive.
    """
    filename = os.path.basename(filepath).replace('_', ' ').replace('-', ' ')
    key_patterns = [
        r'\b([A-G][#b]?)\s*major\b',
        r'\b([A-G][#b]?)\s*maj\b',
        r'\b([A-G][#b]?)major\b',
        r'\b([A-G][#b]?)maj\b',
        r'\b([A-G][#b]?)\s*minor\b',
        r'\b([A-G][#b]?)\s*min\b',
        r'\b([A-G][#b]?)minor\b',
        r'\b([A-G][#b]?)min\b',
        r'\b([A-G][#b]?)\s*[mM]\b',
    ]
    for pattern in key_patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            note = _normalize_root(match.group(1))
            key_text = match.group(0)
            lowered = key_text.lower()
            # classify minor vs major: 'min' or 'minor' anywhere; 'maj'
            # anywhere is major; otherwise the bare one-letter suffix
            # decides by case (M = major, m = minor).
            if "min" in lowered:
                return f"{note}m"
            if "maj" in lowered:
                return note
            if key_text.rstrip().endswith("M"):
                return note
            return f"{note}m"
    return None


# whole-token key regex: A, A#, Ab, Am, A#m, Gbm, etc.
# Lowercase m for minor; capital M rejected to avoid false positives (file "SCREAM").
_BARE_KEY_TOKEN = re.compile(r"^([A-G])([#b]?)(m)?$")


def parse_bare_key_token(token):
    """If `token` is a whole-token musical key (e.g. 'A#', 'Em', 'Bbm'), return
    the normalized 'C#m' / 'Eb' form. Otherwise None.
    """
    m = _BARE_KEY_TOKEN.match(token)
    if not m:
        return None
    note = m.group(1).upper()
    accidental = m.group(2) or ""
    minor = "m" if m.group(3) else ""
    root = _normalize_root(note + accidental)
    return root + minor


# chord-quality tokens that fix the mode of a bare key letter sitting beside them
# (e.g. "min_C", "Maj7_C" -- quality-before-letter, which parse_key_from_filename's
# letter-first patterns miss). Deliberately strict: min/maj plus an optional chord
# extension and nothing else, so "Minimal"/"Magic" don't read as a mode.
_MODE_MIN_TOKEN = re.compile(r"^min(or)?(add)?\d*$", re.I)
_MODE_MAJ_TOKEN = re.compile(r"^maj(or)?(add)?\d*$", re.I)


def _mode_from_neighbour(tokens, i):
    """'min'/'maj' implied by a chord-quality token immediately beside tokens[i]."""
    for j in (i - 1, i + 1):
        if 0 <= j < len(tokens):
            if _MODE_MIN_TOKEN.match(tokens[j]):
                return "min"
            if _MODE_MAJ_TOKEN.match(tokens[j]):
                return "maj"
    return None


def parse_key_from_path(filepath, max_parent_depth=3):
    """Robust key extraction across filename + parent folders.

    Tries parse_key_from_filename first (matches 'Am', 'C minor', etc.),
    then falls back to whole-token bare-key matches in the filename and
    up to `max_parent_depth` parent folders. Returns the first hit or None.

    Whole-token matching avoids false positives like "Analog" matching 'A'.
    """
    existing = parse_key_from_filename(filepath)
    if existing is not None:
        return existing

    # walk filename basename + parent dirs outward
    segments = []
    stem = os.path.splitext(os.path.basename(filepath))[0]
    segments.append(stem)
    cur = os.path.dirname(filepath)
    for _ in range(max_parent_depth):
        if not cur or cur in ("/", "\\"):
            break
        parent = os.path.basename(cur)
        if parent:
            segments.append(parent)
        new_cur = os.path.dirname(cur)
        if new_cur == cur:
            break
        cur = new_cur

    token_re = re.compile(r"[_\-\.\s]+")
    for seg in segments:
        tokens = [t for t in token_re.split(seg) if t]
        for i, token in enumerate(tokens):
            key = parse_bare_key_token(token)
            if key is None:
                continue
            # parse_bare_key_token yields a bare major letter here (minor tokens
            # like 'Am' are caught earlier by parse_key_from_filename). A chord-
            # quality marker beside it fixes the mode: 'min_C' -> 'Cm'.
            if not key.endswith("m") and _mode_from_neighbour(tokens, i) == "min":
                return key + "m"
            return key
    return None


# ── Validation / improvement ───────────────────────────────────────

def validate_and_improve_bpm(detected_bpm, filename_bpm, confidence_threshold=20):
    """
    Validate detected BPM against filename BPM and choose the best value.

    Returns:
        (final_bpm, source) where source is 'detected', 'filename', or 'corrected'.
    """
    if filename_bpm is None:
        return detected_bpm, 'detected'
    if detected_bpm is None:
        return filename_bpm, 'filename'
    if not (60 <= detected_bpm <= 200):
        return filename_bpm, 'filename'

    diff = abs(detected_bpm - filename_bpm)

    if diff <= confidence_threshold:
        return detected_bpm, 'detected'
    if abs(detected_bpm * 2 - filename_bpm) <= confidence_threshold:
        return detected_bpm * 2, 'corrected'
    if abs(detected_bpm / 2 - filename_bpm) <= confidence_threshold:
        return detected_bpm / 2, 'corrected'
    if abs(detected_bpm * 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm * 1.5, 'corrected'
    if abs(detected_bpm / 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm / 1.5, 'corrected'

    return filename_bpm, 'filename'


def improve_key_detection(detected_key, filename_key):
    """
    Combine detected key with filename key for better accuracy.

    Returns:
        (final_key, source) where source is 'detected' or 'filename'.
    """
    if filename_key is None:
        return detected_key, 'detected'
    if detected_key is None:
        return filename_key, 'filename'
    if detected_key == filename_key:
        return detected_key, 'detected'
    return filename_key, 'filename'


# ── Krumhansl-Schmuckler key finding ───────────────────────────────

# Krumhansl-Kessler key profiles: per-pitch-class "fit" weights from the
# probe-tone experiments, indexed from the tonic (index 0 = tonic). Major and
# minor have different shapes, so correlating a piece's pitch-class distribution
# against all 24 rotations names the key AND its mode -- which chroma argmax
# (strongest single pitch) cannot.
_KS_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_KS_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
_PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# minimum K-S correlation to trust an audio-derived key. A clear tonal passage
# correlates ~0.8+; a weakly-tonal or ambiguous input (an isolated chord, a drum
# loop) scores lower and its winning key is unreliable, so below this we emit no
# key rather than a confident-sounding wrong one. A wrong key misroutes harmonic
# search; "unknown" does not.
KEY_CONF_MIN = 0.75


def _pearson(a, b):
    """Pearson correlation of two equal-length sequences; 0 when either is flat."""
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return 0.0
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / (va ** 0.5 * vb ** 0.5)


def estimate_key_ks(chroma12):
    """Krumhansl-Schmuckler key finding over a 12-bin pitch-class distribution.

    ``chroma12`` is 12 energies, one per pitch class starting at C (bin 0 = C,
    as librosa's chroma is laid out), summed/averaged over time. Correlates it
    against the major and minor profile rotated to each of the 12 tonics and
    returns ``(key, confidence)`` -- key is 'C'/'C#'/... for major or 'Cm'/... for
    minor, confidence is the winning Pearson correlation (0..1). Returns
    ``(None, 0.0)`` for an empty or silent (flat/zero) distribution.

    Pure stdlib, so it runs anywhere the 12-bin chroma is available."""
    if not chroma12 or len(chroma12) != 12 or sum(chroma12) <= 0:
        return None, 0.0
    best_key, best_r = None, -2.0
    for tonic in range(12):
        maj = [_KS_MAJOR[(i - tonic) % 12] for i in range(12)]
        minr = [_KS_MINOR[(i - tonic) % 12] for i in range(12)]
        r_maj = _pearson(chroma12, maj)
        if r_maj > best_r:
            best_r, best_key = r_maj, _PITCH_CLASSES[tonic]
        r_min = _pearson(chroma12, minr)
        if r_min > best_r:
            best_r, best_key = r_min, _PITCH_CLASSES[tonic] + "m"
    return best_key, round(max(best_r, 0.0), 3)


# ── Librosa-based estimation ───────────────────────────────────────

def estimate_librosa_metadata(filepath):
    """
    Estimate BPM/key/duration using librosa + filename parsing.

    Returns dict with keys: estimated_bpm, estimated_key, duration_sec,
    bpm_source, key_source, filename_bpm, filename_key, detected_bpm, detected_key.
    """
    import warnings
    warnings.filterwarnings("ignore")

    try:
        import librosa
        import numpy as np
    except ImportError:
        # no audio analysis available, but filename/path parsing is pure Python:
        # still answer from the name rather than losing the capability entirely.
        from acidcat.util.deps import require
        require("librosa", "numpy", group="analysis")
        fn_bpm = parse_bpm_from_filename(filepath)
        fn_key = parse_key_from_path(filepath)
        return {
            "estimated_bpm": fn_bpm,
            "estimated_key": fn_key,
            "duration_sec": None,
            "bpm_source": "filename" if fn_bpm is not None else None,
            "key_source": "filename" if fn_key is not None else None,
            "filename_bpm": fn_bpm,
            "filename_key": fn_key,
            "detected_bpm": None,
            "detected_key": None,
        }

    try:
        y, sr = librosa.load(filepath, sr=None, mono=True)
        duration_sec = round(len(y) / sr, 4) if sr and len(y) > 0 else None

        if len(y) < 256:
            return {
                "estimated_bpm": "oneshot",
                "estimated_key": None,
                "duration_sec": duration_sec,
                "bpm_source": "oneshot",
                "key_source": None,
            }

        # BPM
        detected_bpm = None
        try:
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            tempos_1 = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
            tempos_2 = librosa.beat.tempo(y=y, sr=sr, aggregate=None)
            all_tempos = []
            if tempos_1.size > 0:
                all_tempos.extend(tempos_1)
            if tempos_2.size > 0:
                all_tempos.extend(tempos_2)
            if all_tempos:
                detected_bpm = round(float(np.median(all_tempos)), 2)
        except Exception:
            pass

        filename_bpm = parse_bpm_from_filename(filepath)
        final_bpm, bpm_source = validate_and_improve_bpm(detected_bpm, filename_bpm)

        # Key. Correlate the piece's pitch-class distribution against the
        # Krumhansl-Schmuckler major + minor profiles (estimate_key_ks): unlike
        # chroma argmax, comparing the distribution *shape* against two templates
        # names the mode (major vs minor), so we can emit a real key from audio.
        # chroma_cqt is more tuning-robust than chroma_stft for key finding.
        detected_key, detected_key_conf = None, 0.0
        try:
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr)         # 12 x frames, bin 0 = C
            profile = [float(x) for x in chroma.mean(axis=1)]
            detected_key, detected_key_conf = estimate_key_ks(profile)
            if detected_key_conf < KEY_CONF_MIN:                    # too ambiguous to trust
                detected_key = None
        except Exception:
            pass

        filename_key = parse_key_from_filename(filepath)
        final_key, key_source = improve_key_detection(detected_key, filename_key)

        return {
            "estimated_bpm": final_bpm,
            "estimated_key": final_key,
            "duration_sec": duration_sec,
            "bpm_source": bpm_source,
            "key_source": key_source,
            "filename_bpm": filename_bpm,
            "filename_key": filename_key,
            "detected_bpm": detected_bpm,
            "detected_key": detected_key,
            "detected_key_confidence": detected_key_conf,
        }

    except Exception:
        return {
            "estimated_bpm": None,
            "estimated_key": None,
            "duration_sec": None,
            "bpm_source": "failed",
            "key_source": "failed",
            "filename_bpm": parse_bpm_from_filename(filepath),
            "filename_key": parse_key_from_filename(filepath),
            "detected_bpm": None,
            "detected_key": None,
        }
