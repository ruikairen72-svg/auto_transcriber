"""
Audio extraction from video files using ffmpeg.
"""

from __future__ import annotations

import subprocess
import os
from config import SAMPLE_RATE, AUDIO_CHANNELS


class AudioExtractionError(Exception):
    """Raised when ffmpeg fails to extract audio."""


def extract_audio(
    video_path: str,
    output_dir: str,
    start_time: float | None = None,
    end_time: float | None = None,
) -> str:
    """
    Extract audio from a video file as 16 kHz mono WAV.

    Parameters
    ----------
    video_path : str
        Path to the input MP4 file.
    output_dir : str
        Directory to write the extracted WAV file into.
    start_time : float or None
        Optional start time in seconds for segment extraction.
    end_time : float or None
        Optional end time in seconds for segment extraction.

    Returns
    -------
    str
        Path to the extracted WAV file.

    Raises
    ------
    AudioExtractionError
        If ffmpeg returns a non-zero exit code or the output file is missing.
    FileNotFoundError
        If the input video file does not exist.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, "audio.wav")

    cmd = [
        "/opt/homebrew/bin/ffmpeg",
        "-y",                     # overwrite output
    ]

    # Segment extraction: -ss before -i for fast seeking
    if start_time is not None:
        cmd += ["-ss", str(start_time)]
    if end_time is not None:
        cmd += ["-to", str(end_time)]

    cmd += [
        "-i", video_path,
        "-ac", str(AUDIO_CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-sample_fmt", "s16",
        "-f", "wav",
        output_path,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise AudioExtractionError(
            f"ffmpeg failed (exit {result.returncode}):\n{result.stderr.strip()}"
        )

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise AudioExtractionError("ffmpeg completed but no output file was produced.")

    return output_path
