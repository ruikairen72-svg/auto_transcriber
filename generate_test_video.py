#!/usr/bin/env python3
"""
Generate a synthetic test MP4 for end-to-end pipeline verification.

Creates a short video with alternating frequency tones (simulating different
"speakers") so the diarizer has something to separate.

Usage:
    python generate_test_video.py          # creates test_files/test.mp4
    python generate_test_video.py --dur 30 # 30-second video
"""

import subprocess
import os
import sys
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "test_files")
OUTPUT_MP4 = os.path.join(OUTPUT_DIR, "test.mp4")


def generate_sine_tone(freq: int, duration: float, output_path: str):
    """Generate a sine wave WAV at the given frequency."""
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={duration}:sample_rate=16000",
        "-ac", "1",
        "-ar", "16000",
        "-sample_fmt", "s16",
        output_path,
    ], capture_output=True, check=True)


def generate_silence(duration: float, output_path: str):
    """Generate silence."""
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r=16000:cl=mono:d={duration}",
        "-ac", "1",
        "-ar", "16000",
        "-sample_fmt", "s16",
        output_path,
    ], capture_output=True, check=True)


def concat_wavs(wav_paths: list[str], output_path: str):
    """Concatenate WAV files using ffmpeg concat demuxer."""
    concat_list = os.path.join(OUTPUT_DIR, "concat.txt")
    with open(concat_list, "w") as f:
        for p in wav_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list,
        "-ac", "1",
        "-ar", "16000",
        "-sample_fmt", "s16",
        output_path,
    ], capture_output=True, check=True)

    os.remove(concat_list)


def wav_to_mp4(wav_path: str, mp4_path: str):
    """Mux WAV into an MP4 container with a black video track."""
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "color=c=black:s=320x240:r=1",
        "-i", wav_path,
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        "-pix_fmt", "yuv420p",
        mp4_path,
    ], capture_output=True, check=True)


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic test MP4")
    parser.add_argument("--dur", type=float, default=15,
                        help="Total duration in seconds (default 15)")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    total = args.dur
    # Three "speakers" with distinct frequencies, interleaved with silence
    # Speaker A: 300 Hz, Speaker B: 600 Hz, Speaker C: 900 Hz
    segment_dur = total / 6.0  # 6 segments alternating
    silence_dur = 0.5

    print(f"Generating {total}s test video with 3 simulated speakers...")

    wavs = []
    speakers = [300, 600, 900]
    for i in range(3):  # 3 rounds
        for j, freq in enumerate(speakers):
            seg_path = os.path.join(OUTPUT_DIR, f"seg_{i}_{j}.wav")
            generate_sine_tone(freq, segment_dur, seg_path)
            wavs.append(seg_path)

            sil_path = os.path.join(OUTPUT_DIR, f"sil_{i}_{j}.wav")
            generate_silence(silence_dur, sil_path)
            wavs.append(sil_path)

    # Concatenate all segments
    combined_wav = os.path.join(OUTPUT_DIR, "test_audio.wav")
    concat_wavs(wavs, combined_wav)
    print(f"  Combined audio: {combined_wav}")

    # Mux into MP4
    wav_to_mp4(combined_wav, OUTPUT_MP4)
    print(f"  Test video: {OUTPUT_MP4}")

    # Clean up temporary WAVs
    for f in os.listdir(OUTPUT_DIR):
        if f.startswith("seg_") or f.startswith("sil_") or f == "test_audio.wav":
            os.remove(os.path.join(OUTPUT_DIR, f))

    # Verify
    size_mb = os.path.getsize(OUTPUT_MP4) / (1024 * 1024)
    print(f"\n✅ Done! Test video created ({size_mb:.1f} MB)")
    print(f"   Path: {OUTPUT_MP4}")


if __name__ == "__main__":
    main()
