"""
Multi-dimensional acoustic feature extraction for speaker characterisation.

Used by the child-speaker identification pipeline to compute per-speaker
features that discriminate children from adults (pitch, formants, MFCC
variability, spectral centroid, speech rate).
"""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
import warnings

import numpy as np

# librosa emits FutureWarning about ``librosa.pyin`` deprecation via
# ``librosa.util.example`` — suppress for clean production output.
warnings.filterwarnings("ignore", category=FutureWarning, module="librosa")

import librosa
import soundfile as sf

from config import SAMPLE_RATE

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum seconds of audio to analyse per speaker (limit CPU / memory)
_MAX_ANALYSIS_DURATION = 30.0

# Number of MFCC coefficients
_N_MFCC = 13

# LPC order for formant estimation
_LPC_ORDER = 12

# pyin pitch range (Hz) — captures both adult male (~100 Hz) and child (~300 Hz)
_F0_MIN = 80
_F0_MAX = 600

# Formant search range (Hz) per formant — typical child / adult female ranges
_FORMANT_RANGES = [
    (250, 1200),   # F1
    (700, 3000),   # F2
    (1800, 4500),  # F3
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_speaker_audio(
    audio_path: str,
    segments: list[dict],
    temp_dir: str,
) -> str | None:
    """Concatenate one speaker's segments into a single WAV (≤ 30 s)."""
    if not segments:
        return None

    # Collect segment audio via ffmpeg, concatenating into one file
    total_dur = 0.0
    seg_files: list[str] = []

    for i, seg in enumerate(segments):
        if total_dur >= _MAX_ANALYSIS_DURATION:
            break
        start = seg["start"]
        dur = min(seg["end"] - seg["start"], _MAX_ANALYSIS_DURATION - total_dur)
        if dur <= 0.05:  # skip ultra-short segments
            continue
        out_seg = os.path.join(temp_dir, f"_spk_seg_{i}.wav")
        cmd = [
            "/opt/homebrew/bin/ffmpeg", "-y", "-loglevel", "error",
            "-ss", str(start), "-t", str(dur),
            "-i", audio_path,
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-sample_fmt", "s16",
            out_seg,
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            seg_files.append(out_seg)
            total_dur += dur
        except subprocess.CalledProcessError:
            continue

    if not seg_files:
        return None

    # Concatenate segments
    concat_list = os.path.join(temp_dir, "_concat_list.txt")
    with open(concat_list, "w") as f:
        for sf_path in seg_files:
            f.write(f"file '{sf_path}'\n")

    out_path = os.path.join(temp_dir, "_speaker_concat.wav")
    cmd = [
        "/opt/homebrew/bin/ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-sample_fmt", "s16",
        out_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError:
        return None

    # Clean up segment files
    for sf_path in seg_files:
        try:
            os.remove(sf_path)
        except OSError:
            pass

    return out_path


def _extract_f0(y: np.ndarray, sr: int) -> dict:
    """Extract fundamental frequency (F0) statistics via pYIN."""
    f0, voiced_flag, _ = librosa.pyin(
        y, fmin=_F0_MIN, fmax=_F0_MAX, sr=sr,
        fill_na=np.nan,
    )
    voiced = f0[voiced_flag]
    if len(voiced) < 5:
        return {"f0_median": 0.0, "f0_iqr": 0.0, "f0_values": np.array([])}

    return {
        "f0_median": float(np.nanmedian(voiced)),
        "f0_iqr": float(np.subtract(*np.nanpercentile(voiced, [75, 25]))),
        "f0_values": voiced,
    }


def _extract_formants(y: np.ndarray, sr: int) -> dict:
    """Estimate F1, F2, F3 via LPC per frame."""
    frame_len = int(0.025 * sr)   # 25 ms
    hop_len = int(0.010 * sr)     # 10 ms

    formants: list[tuple[float, float, float]] = []

    for start in range(0, len(y) - frame_len, hop_len):
        frame = y[start : start + frame_len]
        if len(frame) < frame_len:
            break
        # Hamming window
        frame = frame * np.hamming(len(frame))

        try:
            lpc = librosa.lpc(frame, order=_LPC_ORDER)
        except Exception:
            continue

        # Find roots of the LPC polynomial → formant frequencies
        roots = np.roots(lpc)
        # Keep roots with positive imaginary part and inside unit circle
        roots = [r for r in roots if np.imag(r) > 0 and np.abs(r) < 0.99]
        if not roots:
            continue

        # Formant frequencies from root angles
        freqs = np.sort([np.abs(np.angle(r) * sr / (2 * np.pi)) for r in roots])

        # Pick peaks in expected formant ranges
        frame_formants: list[float] = []
        for f_min, f_max in _FORMANT_RANGES:
            candidates = freqs[(freqs >= f_min) & (freqs <= f_max)]
            if len(candidates) > 0:
                frame_formants.append(float(candidates[0]))
            else:
                frame_formants.append(float(np.nan))

        if len(frame_formants) >= 3:
            formants.append(tuple(frame_formants[:3]))

    if not formants:
        return {
            "f1_mean": 0.0, "f1_std": 0.0,
            "f2_mean": 0.0, "f2_std": 0.0,
            "f3_mean": 0.0, "f3_std": 0.0,
        }

    f1s = [f[0] for f in formants if not np.isnan(f[0])]
    f2s = [f[1] for f in formants if not np.isnan(f[1])]
    f3s = [f[2] for f in formants if not np.isnan(f[2])]

    return {
        "f1_mean": float(np.mean(f1s)) if f1s else 0.0,
        "f1_std": float(np.std(f1s)) if f1s else 0.0,
        "f2_mean": float(np.mean(f2s)) if f2s else 0.0,
        "f2_std": float(np.std(f2s)) if f2s else 0.0,
        "f3_mean": float(np.mean(f3s)) if f3s else 0.0,
        "f3_std": float(np.std(f3s)) if f3s else 0.0,
    }


def _extract_mfcc_variability(y: np.ndarray, sr: int) -> float:
    """Compute mean across-coefficient variance of 13-dim MFCC."""
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=_N_MFCC)
    # Variance of each coefficient over time, then average over coefficients
    var_per_coeff = np.var(mfcc, axis=1)
    return float(np.mean(var_per_coeff))


def _extract_spectral_centroid(y: np.ndarray, sr: int) -> dict:
    """Compute spectral centroid mean and standard deviation."""
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    return {
        "centroid_mean": float(np.mean(centroid)),
        "centroid_std": float(np.std(centroid)),
    }


def _extract_speech_rate(segments: list[dict]) -> dict:
    """Estimate speech rate (words/sec) and pause statistics from timestamps."""
    if len(segments) < 1:
        return {"speech_rate": 0.0, "mean_pause_dur": 0.0}

    total_words = 0
    total_speech_dur = 0.0
    pauses: list[float] = []

    for i, seg in enumerate(segments):
        text = seg.get("text", "")
        words = text.split()
        total_words += len(words)
        total_speech_dur += seg["end"] - seg["start"]

        if i > 0:
            pause = seg["start"] - segments[i - 1]["end"]
            if pause > 0.05:  # ignore <50ms — likely alignment artifacts
                pauses.append(pause)

    speech_rate = total_words / total_speech_dur if total_speech_dur > 0 else 0.0
    mean_pause = float(np.mean(pauses)) if pauses else 0.0

    return {"speech_rate": speech_rate, "mean_pause_dur": mean_pause}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_speaker_features(
    audio_path: str,
    aligned_segments: list[dict],
) -> dict[str, dict]:
    """
    Compute multi-dimensional acoustic features for each speaker.

    Parameters
    ----------
    audio_path : str
        Path to the full extracted WAV file.
    aligned_segments : list[dict]
        ``[{start, end, text, speaker}, ...]`` as returned by ``align_segments``.

    Returns
    -------
    dict[str, dict]
        ``{speaker_id: {f0_median, f0_iqr, f1_mean, ..., centroid_std, ...}}``
        On extraction failure for a speaker, returns ``{"fallback": True, ...}``
        with at least ``f0_median`` populated via a simplified estimate.
    """
    # Group segments by speaker
    speaker_segs: dict[str, list[dict]] = {}
    for seg in aligned_segments:
        spk = seg["speaker"]
        if spk not in speaker_segs:
            speaker_segs[spk] = []
        speaker_segs[spk].append(seg)

    result: dict[str, dict] = {}

    with tempfile.TemporaryDirectory(prefix="spk_feat_") as tmp:
        for spk_id, segs in speaker_segs.items():
            try:
                # 1. Gather speaker audio
                concat_path = _load_speaker_audio(audio_path, segs, tmp)
                if concat_path is None or not os.path.isfile(concat_path):
                    raise ValueError("no speaker audio produced")

                y, sr = librosa.load(concat_path, sr=SAMPLE_RATE, mono=True)
                if len(y) < sr * 0.5:  # < 0.5 s of audio
                    raise ValueError("audio too short (< 0.5 s)")

                # 2. Extract all features
                f0_feats = _extract_f0(y, sr)
                formant_feats = _extract_formants(y, sr)
                mfcc_var = _extract_mfcc_variability(y, sr)
                centroid_feats = _extract_spectral_centroid(y, sr)
                rate_feats = _extract_speech_rate(segs)

                result[spk_id] = {
                    **f0_feats,
                    **formant_feats,
                    "mfcc_variability": mfcc_var,
                    **centroid_feats,
                    **rate_feats,
                    "fallback": False,
                }

                # Clean up concat file
                try:
                    os.remove(concat_path)
                except OSError:
                    pass

            except Exception:
                # Fallback: use a simple F0 estimate from the whole audio
                # (if possible) or mark as fallback
                try:
                    y_full, sr_full = librosa.load(
                        audio_path, sr=SAMPLE_RATE, mono=True,
                        duration=_MAX_ANALYSIS_DURATION,
                    )
                    f0_feats = _extract_f0(y_full, sr_full)
                    result[spk_id] = {
                        "f0_median": f0_feats.get("f0_median", 0.0),
                        "f0_iqr": f0_feats.get("f0_iqr", 0.0),
                        "fallback": True,
                    }
                except Exception:
                    result[spk_id] = {
                        "f0_median": 0.0,
                        "f0_iqr": 0.0,
                        "fallback": True,
                    }

    return result
