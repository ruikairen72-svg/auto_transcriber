"""
Processor package — convenience imports and pipeline orchestrator.

All heavy imports are lazy (inside functions) so that individual modules
can be imported and tested without pulling in every ML dependency.
"""

from __future__ import annotations

import os
import shutil


def run_pipeline(video_path: str, temp_dir: str, hf_token: str = None,
                  num_speakers: int = None,
                  start_time: float = None,
                  end_time: float = None,
                  progress_callback=None) -> dict:
    """
    Run the complete processing pipeline on an MP4 video.

    1. Extract audio (ffmpeg → 16 kHz mono WAV)
    2. Transcribe (faster-whisper)
    3. Diarize (pyannote.audio 3.1)
    4. Align transcription × diarization timestamps
    5. Restore punctuation (FunASR ct-punc → English mapping)
    6. Split into individual sentences
    7. Extract representative speaker audio clips

    Parameters
    ----------
    video_path : str
        Path to the uploaded MP4 file.
    temp_dir : str
        Directory for intermediate and output files.
    hf_token : str or None
        HuggingFace token for pyannote (falls back to env ``HF_TOKEN``).
    num_speakers : int or None
        Expected number of speakers.  When ``None`` (default), the diarizer
        uses automatic detection with ``min=1, max=6``.  Set to a specific
        number (e.g. 2, 3) to constrain the diarizer.
    start_time : float or None
        Optional start time in seconds for video segment extraction.
    end_time : float or None
        Optional end time in seconds for video segment extraction.
    progress_callback : callable or None
        Optional ``(amount: float, desc: str) -> None`` called after each
        stage so the caller can update a Gradio ``gr.Progress`` bar.

    Returns
    -------
    dict
        Keys:
        - ``aligned_segments``: list[dict]  — ``{start, end, text, speaker}``
        - ``speakers``: list[str]           — unique speaker IDs in appearance order
        - ``speaker_clips``: dict[str, str] — speaker ID → clip WAV path
        - ``transcript_samples``: dict[str, list[str]] — speaker ID → sample texts
        - ``audio_path``: str               — path to extracted WAV
        - ``segment_offset``: float         — start_time used for timestamp adjustment
    """
    # Lazy imports — avoids loading torch / faster-whisper / pyannote at
    # package-import time.
    from .audio_extractor import extract_audio
    from .transcriber import transcribe
    from .diarizer import diarize
    from .aligner import align_segments, extract_speaker_clips, get_transcript_samples
    from .punctuation import restore_punctuation

    # 1. Extract audio
    if progress_callback:
        progress_callback(0.12, "🔊 Extracting audio...")
    audio_path = extract_audio(video_path, temp_dir,
                               start_time=start_time, end_time=end_time)

    # 2. Transcribe
    if progress_callback:
        progress_callback(0.28, "📝 Transcribing (faster-whisper)...")
    segments = transcribe(audio_path)

    # 3. Diarize
    if progress_callback:
        progress_callback(0.48, "👥 Speaker diarization (pyannote.audio)...")
    diarization = diarize(audio_path, hf_token, num_speakers=num_speakers)

    # 4. Align — intersect whisper segments with pyannote speaker turns
    if progress_callback:
        progress_callback(0.64, "🔗 Aligning timestamps...")
    aligned = align_segments(segments, diarization)

    # 5. Punctuation restoration — FunASR ct-punc + Chinese→English mapping
    if progress_callback:
        progress_callback(0.76, "📖 Restoring punctuation (FunASR ct-punc)...")
    aligned = restore_punctuation(aligned)

    # Collect unique speakers in order of first appearance
    seen = []
    for seg in aligned:
        spk = seg["speaker"]
        if spk not in seen:
            seen.append(spk)

    # 6. Extract first-sentence audio clip per speaker (for UI playback)
    #    IMPORTANT: must happen BEFORE timestamp adjustment so clip
    #    timestamps match the extracted audio (not the original video).
    if progress_callback:
        progress_callback(0.82, "✂️ Extracting speaker clips...")
    clips = extract_speaker_clips(audio_path, aligned, temp_dir)

    # 7. Collect 2-3 transcript samples per speaker (for UI display)
    samples = get_transcript_samples(aligned, max_samples=3)

    # 8. Adjust timestamps to original video time (add segment offset)
    #    Must happen AFTER clip extraction so clips use the audio-relative
    #    timestamps.  EAF export uses the adjusted timestamps.
    segment_offset = start_time or 0.0
    if segment_offset > 0:
        for seg in aligned:
            seg["start"] = round(seg["start"] + segment_offset, 2)
            seg["end"] = round(seg["end"] + segment_offset, 2)

    return {
        "aligned_segments": aligned,
        "speakers": seen,
        "speaker_clips": clips,
        "transcript_samples": samples,
        "audio_path": audio_path,
        "segment_offset": segment_offset,
    }


def cleanup_temp(temp_dir: str) -> None:
    """Remove and recreate the temporary directory."""
    if os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
