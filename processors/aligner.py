"""
Align transcription segments with speaker diarization results.

Also handles extraction of representative audio clips per speaker
(first complete sentence — most natural for human identification).
"""

import os
import subprocess

from config import SAMPLE_RATE, AUDIO_CHANNELS, CLIP_DURATION


def align_segments(
    transcription: list[dict],
    diarization: list[dict],
) -> list[dict]:
    """
    Assign a speaker to each transcription segment by maximum time overlap.

    Parameters
    ----------
    transcription : list[dict]
        Segments from ``transcriber.transcribe`` — ``{start, end, text}``.
    diarization : list[dict]
        Segments from ``diarizer.diarize`` — ``{start, end, speaker}``.

    Returns
    -------
    list[dict]
        Segments annotated with ``speaker``: ``{start, end, text, speaker}``.
    """
    aligned = []

    for t_seg in transcription:
        best_speaker = None
        best_overlap = 0.0

        for d_seg in diarization:
            overlap_start = max(t_seg["start"], d_seg["start"])
            overlap_end = min(t_seg["end"], d_seg["end"])
            overlap = max(0.0, overlap_end - overlap_start)

            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = d_seg["speaker"]

        if best_speaker is None:
            # No overlap — fall back to SPEAKER_UNKNOWN
            best_speaker = "SPEAKER_UNKNOWN"

        aligned.append({
            "start": t_seg["start"],
            "end": t_seg["end"],
            "text": t_seg["text"],
            "speaker": best_speaker,
            "words": t_seg.get("words", []),
        })

    # Merge consecutive segments that belong to the same speaker
    merged = _merge_consecutive(aligned)
    merged = _reassign_unknown_speakers(merged)
    return merged


def _merge_consecutive(segments: list[dict], gap_threshold: float = 0.2) -> list[dict]:
    """Combine adjacent segments from the same speaker.

    Segments are only merged when the silence gap between them is ≤
    *gap_threshold* seconds.  A larger gap means a natural pause in speech,
    so the segments are kept separate even if the speaker is the same.
    """
    if not segments:
        return []

    merged = [segments[0].copy()]
    for seg in segments[1:]:
        prev = merged[-1]
        gap = seg["start"] - prev["end"]
        if seg["speaker"] == prev["speaker"] and gap <= gap_threshold:
            prev["end"] = seg["end"]
            prev["text"] = prev["text"] + " " + seg["text"]
            prev["words"] = prev.get("words", []) + seg.get("words", [])
        else:
            merged.append(seg.copy())
    return merged


def _reassign_unknown_speakers(segments: list[dict]) -> list[dict]:
    """
    Reassign UNKNOWN speaker labels to the nearest known speaker.

    Rules:
    - UNKNOWN segment < 1 s at the **start** or **end** → dropped (likely noise).
    - UNKNOWN segment < 2 s in the middle → reassigned to the nearest known
      speaker by time-distance, ties broken in favour of the preceding speaker.
    - UNKNOWN segment >= 2 s → left as-is (the UNKNOWN label is meaningful).

    Parameters
    ----------
    segments : list[dict]
        ``{start, end, text, speaker, ...}``, already merged by
        ``_merge_consecutive``.

    Returns
    -------
    list[dict]
        Segments with UNKNOWN labels cleaned up.
    """
    if not segments:
        return []

    # Build index of known-speaker segments (by position)
    known_indices = [i for i, s in enumerate(segments) if s["speaker"] != "SPEAKER_UNKNOWN"]

    if not known_indices:
        # All UNKNOWN — nothing to reassign to; just drop leading/trailing noise
        result = segments[:]
    else:
        result = []
        for i, seg in enumerate(segments):
            if seg["speaker"] != "SPEAKER_UNKNOWN":
                result.append(seg)
                continue

            duration = seg["end"] - seg["start"]

            # Leading UNKNOWN < 1 s → drop
            if i < known_indices[0] and duration < 1.0:
                continue

            # Trailing UNKNOWN < 1 s → drop
            if i > known_indices[-1] and duration < 1.0:
                continue

            # Middle UNKNOWN < 2 s → reassign to nearest known speaker
            if duration < 2.0:
                # Find nearest known neighbour by time distance
                nearest_dist = float("inf")
                nearest_speaker = None
                for ki in known_indices:
                    if ki < i:
                        dist = seg["start"] - segments[ki]["end"]
                    else:
                        dist = segments[ki]["start"] - seg["end"]
                    if dist < 0:
                        dist = abs(dist)
                    if dist < nearest_dist:
                        nearest_dist = dist
                        nearest_speaker = segments[ki]["speaker"]

                if nearest_speaker is not None:
                    seg = seg.copy()
                    seg["speaker"] = nearest_speaker

                result.append(seg)
            else:
                # Long UNKNOWN — keep as-is
                result.append(seg)

    return result


# ---------------------------------------------------------------------------
# Representative audio clip extraction
# ---------------------------------------------------------------------------

def extract_speaker_clips(
    audio_path: str,
    aligned_segments: list[dict],
    temp_dir: str,
    clip_duration: float = None,
) -> dict[str, str]:
    """
    Extract a short representative audio clip for each unique speaker.

    Uses the **first** contiguous segment for each speaker (the most natural
    choice for human voice identification) and trims it to *clip_duration*
    seconds.

    Parameters
    ----------
    audio_path : str
        Path to the full extracted WAV.
    aligned_segments : list[dict]
        Output of ``align_segments`` — ``[{start, end, text, speaker}, ...]``.
    temp_dir : str
        Directory for clip files.
    clip_duration : float or None
        Seconds to keep (defaults to ``CLIP_DURATION`` from config).

    Returns
    -------
    dict[str, str]
        Mapping ``{speaker_id: clip_wav_path}``.
    """
    if clip_duration is None:
        clip_duration = CLIP_DURATION

    os.makedirs(temp_dir, exist_ok=True)

    # Collect the FIRST segment for each speaker (not the longest).
    # The first utterance is the most natural sample for a human listener.
    speaker_first_seg: dict[str, dict] = {}
    for seg in aligned_segments:
        spk = seg["speaker"]
        if spk not in speaker_first_seg:
            speaker_first_seg[spk] = seg

    clips = {}
    for spk, seg in speaker_first_seg.items():
        clip_start = seg["start"]
        clip_end = min(seg["end"], clip_start + clip_duration)

        out_path = os.path.join(temp_dir, f"clip_{spk}.wav")
        cmd = [
            "/opt/homebrew/bin/ffmpeg", "-y",
            "-i", audio_path,
            "-ss", str(clip_start),
            "-to", str(clip_end),
            "-ac", str(AUDIO_CHANNELS),
            "-ar", str(SAMPLE_RATE),
            "-sample_fmt", "s16",
            out_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        clips[spk] = out_path

    return clips


# ---------------------------------------------------------------------------
# Transcript samples
# ---------------------------------------------------------------------------

def get_transcript_samples(
    aligned_segments: list[dict],
    max_samples: int = 3,
) -> dict[str, list[str]]:
    """
    Collect up to *max_samples* transcript texts per speaker.

    Returns
    -------
    dict[str, list[str]]
        ``{speaker_id: [text, text, ...]}``
    """
    samples = {}
    for seg in aligned_segments:
        spk = seg["speaker"]
        if spk not in samples:
            samples[spk] = []
        if len(samples[spk]) < max_samples:
            samples[spk].append(seg["text"])
    return samples


# ---------------------------------------------------------------------------
# Child speaker identification via multi-dimensional acoustic features
# ---------------------------------------------------------------------------

def compute_child_score(features: dict) -> float:
    """
    Compute a 0–100 "child confidence" score from extracted acoustic features.

    The score is a weighted combination of sub-scores derived by comparing
    the speaker's features against child-typical reference ranges.  A score
    > 80 strongly suggests a pre-pubescent child speaker.

    Parameters
    ----------
    features : dict
        Output of ``extract_speaker_features`` — contains ``f0_median``,
        ``f1_mean``, ``f2_mean``, ``mfcc_variability``, ``centroid_mean``,
        ``speech_rate``, and optionally ``fallback``.

    Returns
    -------
    float
        Score between 0 (definitely adult) and 100 (definitely child).
    """
    # ---- Fallback path: simple F0 threshold ----
    if features.get("fallback"):
        f0 = features.get("f0_median", 0.0)
        return 85.0 if f0 > 250 else 30.0

    # ---- Sub-score helpers ----
    def _linear_score(value: float, low: float, high: float, weight: float) -> float:
        """Linear ramp: 0 at *low*, *weight* at *high* and above."""
        if value >= high:
            return weight
        if value <= low:
            return 0.0
        return weight * (value - low) / (high - low)

    def _inverse_linear(value: float, low: float, high: float, weight: float) -> float:
        """Inverse linear: *weight* at ≤*low*, 0 at ≥*high*."""
        if value <= low:
            return weight
        if value >= high:
            return 0.0
        return weight * (high - value) / (high - low)

    score = 0.0

    # 1. F0 median (weight 30) — children typically > 250 Hz
    f0 = features.get("f0_median", 0.0)
    score += _linear_score(f0, 180.0, 280.0, 30.0)

    # 2. F1 mean (weight 20) — children: F1 > 400 Hz (shorter vocal tract)
    f1 = features.get("f1_mean", 0.0)
    score += _linear_score(f1, 300.0, 500.0, 20.0)

    # 3. Formant spacing F2-F1 (weight 15) — wider spacing in children
    f2 = features.get("f2_mean", 0.0)
    spacing = abs(f2 - f1) if f1 > 0 and f2 > 0 else 0.0
    score += _linear_score(spacing, 500.0, 1000.0, 15.0)

    # 4. MFCC variability (weight 15) — children have less stable articulation
    mfcc_var = features.get("mfcc_variability", 0.0)
    # Typical adult range: 5-50; child range: 15-150
    score += _linear_score(mfcc_var, 10.0, 60.0, 15.0)

    # 5. Spectral centroid (weight 10) — children: brighter, > 1500 Hz
    cent = features.get("centroid_mean", 0.0)
    score += _linear_score(cent, 800.0, 2000.0, 10.0)

    # 6. Speech rate (weight 10) — children slower, < 4 words/sec
    rate = features.get("speech_rate", 0.0)
    score += _inverse_linear(rate, 2.5, 5.0, 10.0)

    return min(score, 100.0)


def identify_child_speakers(
    audio_path: str,
    aligned_segments: list[dict],
    threshold: float = 80.0,
) -> set[str]:
    """
    Identify child speakers in the aligned segments using multi-dimensional
    acoustic feature fusion.

    Parameters
    ----------
    audio_path : str
        Path to the full extracted WAV file.
    aligned_segments : list[dict]
        ``[{start, end, text, speaker}, ...]``.
    threshold : float
        Minimum ``compute_child_score`` to classify as child (default 80).

    Returns
    -------
    set[str]
        Speaker IDs classified as children.
    """
    from processors.audio_utils import extract_speaker_features

    # Extract per-speaker acoustic features
    all_features = extract_speaker_features(audio_path, aligned_segments)

    # Score each speaker
    child_speakers: set[str] = set()
    print("\n👶 Child speaker analysis:")
    print("-" * 50)

    for spk_id, feats in all_features.items():
        score = compute_child_score(feats)
        f0 = feats.get("f0_median", 0.0)
        f1 = feats.get("f1_mean", 0.0)
        rate = feats.get("speech_rate", 0.0)
        fallback = " (fallback)" if feats.get("fallback") else ""
        verdict = "CHILD" if score > threshold else "adult"
        print(
            f"  {spk_id}: score={score:.1f} → {verdict}"
            f"  (F0={f0:.0f} Hz, F1={f1:.0f} Hz, rate={rate:.1f} w/s){fallback}"
        )

        if score > threshold:
            child_speakers.add(spk_id)

    if child_speakers:
        print(f"\n  ✅ Identified child speaker(s): {', '.join(sorted(child_speakers))}")
    else:
        print("\n  ❌ No child-like speakers detected.")
    print("-" * 50)

    return child_speakers
