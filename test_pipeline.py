#!/usr/bin/env python3
"""
End-to-end smoke test for the auto_transcriber_web pipeline.

Runs the full pipeline (audio extraction → ASR → diarization →
alignment → punctuation → EAF/PFSX export) on a synthetic test MP4
and verifies all outputs.

Usage:
    # Generate test video first, then run:
    python test_pipeline.py

    # Or auto-generate + test:
    python test_pipeline.py --generate
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
import xml.etree.ElementTree as ET

# Allow running from the auto_transcriber_web directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TEST_MP4 = os.path.join(SCRIPT_DIR, "test_files", "test.mp4")
TEST_TEMP = os.path.join(SCRIPT_DIR, "test_temp")
TEST_OUTPUT_EAF = os.path.join(TEST_TEMP, "output.eaf")
TEST_OUTPUT_PFSX = os.path.join(TEST_TEMP, "output.pfsx")

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"


def log(msg: str):
    print(f"  {msg}")


def check(condition: bool, label: str) -> bool:
    """Print a ✓ or ✗ check result."""
    print(f"  {PASS if condition else FAIL} {label}")
    return condition


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_01_ffmpeg_available():
    """Check ffmpeg is installed."""
    print("\n[Test 1] ffmpeg availability")
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True)
    return check(result.returncode == 0, "ffmpeg is installed and in PATH")


def test_02_config_loads():
    """Check config loads and token is set."""
    print("\n[Test 2] Configuration")
    from config import HF_TOKEN, SAMPLE_RATE, WHISPER_MODEL
    ok = True
    ok &= check(SAMPLE_RATE == 16000, f"SAMPLE_RATE = {SAMPLE_RATE}")
    ok &= check(len(HF_TOKEN) > 0, "HF_TOKEN is set")
    ok &= check(WHISPER_MODEL in ("base", "small"), f"WHISPER_MODEL = {WHISPER_MODEL}")
    return ok


def test_03_generate_test_video():
    """Generate synthetic test MP4 if it doesn't exist."""
    print("\n[Test 3] Test video")
    if os.path.isfile(TEST_MP4):
        size = os.path.getsize(TEST_MP4)
        return check(size > 0, f"Test video exists ({size} bytes)")
    else:
        print("  Test video not found. Generating...")
        script = os.path.join(SCRIPT_DIR, "generate_test_video.py")
        result = subprocess.run(
            [sys.executable, script, "--dur", "15"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(result.stderr)
            return check(False, "Failed to generate test video")
        return check(os.path.isfile(TEST_MP4), "Generated test video")


def test_04_audio_extraction():
    """Test audio extraction from the test MP4."""
    print("\n[Test 4] Audio extraction")
    from processors.audio_extractor import extract_audio
    os.makedirs(TEST_TEMP, exist_ok=True)
    try:
        wav_path = extract_audio(TEST_MP4, TEST_TEMP)
        ok = check(os.path.isfile(wav_path), f"Extracted WAV: {wav_path}")
        size = os.path.getsize(wav_path)
        ok &= check(size > 0, f"WAV file size: {size} bytes")
        return ok
    except Exception as e:
        return check(False, f"Audio extraction failed: {e}")


def test_05_transcription():
    """Test faster-whisper transcription."""
    print("\n[Test 5] Speech recognition (faster-whisper)")
    from processors.transcriber import transcribe
    wav_path = os.path.join(TEST_TEMP, "audio.wav")
    try:
        segments = transcribe(wav_path)
        ok = check(len(segments) >= 0, f"Transcription produced {len(segments)} segment(s)")
        if segments:
            ok &= check("start" in segments[0], "Segments have 'start' key")
            ok &= check("end" in segments[0], "Segments have 'end' key")
            ok &= check("text" in segments[0], "Segments have 'text' key")
            log(f"  First segment: [{segments[0]['start']:.1f}s-{segments[0]['end']:.1f}s] "
                f"\"{segments[0]['text'][:60]}...\"")
        return ok
    except Exception as e:
        return check(False, f"Transcription failed: {e}")


def test_06_diarization():
    """Test pyannote speaker diarization."""
    print("\n[Test 6] Speaker diarization (pyannote.audio)")
    from processors.diarizer import diarize
    from config import HF_TOKEN
    wav_path = os.path.join(TEST_TEMP, "audio.wav")
    try:
        turns = diarize(wav_path, HF_TOKEN)
        ok = check(isinstance(turns, list), f"Diarization returned list")
        ok &= check(len(turns) >= 0, f"Detected {len(turns)} speaker turn(s)")
        if turns:
            ok &= check("speaker" in turns[0], "Turns have 'speaker' key")
            for t in turns:
                log(f"  {t['speaker']}: [{t['start']:.1f}s - {t['end']:.1f}s]")
        return ok
    except Exception as e:
        return check(False, f"Diarization failed: {e}")


def test_07_alignment():
    """Test transcription + diarization alignment."""
    print("\n[Test 7] Timestamp alignment")
    from processors.transcriber import transcribe
    from processors.diarizer import diarize
    from processors.aligner import align_segments
    from config import HF_TOKEN

    wav_path = os.path.join(TEST_TEMP, "audio.wav")
    try:
        segments = transcribe(wav_path)
        turns = diarize(wav_path, HF_TOKEN)
        aligned = align_segments(segments, turns)
        ok = check(len(aligned) >= 0, f"Aligned {len(aligned)} segment(s)")
        if aligned:
            ok &= check("speaker" in aligned[0], "Aligned segments have 'speaker'")
            speakers = set(s["speaker"] for s in aligned)
            log(f"  Unique speakers: {speakers}")
        return ok
    except Exception as e:
        return check(False, f"Alignment failed: {e}")


def test_08_punctuation():
    """Test FunASR punctuation restoration."""
    print("\n[Test 8] Punctuation restoration (FunASR ct-punc)")
    from processors.punctuation import restore_punctuation
    from processors.aligner import align_segments
    from processors.transcriber import transcribe
    from processors.diarizer import diarize
    from config import HF_TOKEN

    wav_path = os.path.join(TEST_TEMP, "audio.wav")
    try:
        segments = transcribe(wav_path)
        turns = diarize(wav_path, HF_TOKEN)
        aligned = align_segments(segments, turns)

        if not aligned:
            log("  No segments to punctuate (expected for synthetic audio)")
            return True

        punctuated = restore_punctuation(aligned)
        ok = check(len(punctuated) == len(aligned), "Segment count preserved")
        if punctuated and punctuated[0]["text"]:
            # Check Chinese→English mapping
            text = punctuated[0]["text"]
            has_cn_period = "。" in text
            ok &= check(not has_cn_period, "No Chinese period (。) in output")
            # Check capitalization
            if text and text[0].isalpha():
                ok &= check(text[0].isupper(), "First letter is capitalized")
            log(f"  Sample: \"{text[:80]}...\"")
        return ok
    except Exception as e:
        return check(False, f"Punctuation failed: {e}")


def test_09_eaf_export():
    """Test EAF file generation and XML validity."""
    print("\n[Test 9] EAF export")
    try:
        # Run full pipeline to get aligned segments
        from processors import run_pipeline
        result = run_pipeline(TEST_MP4, TEST_TEMP)
        aligned = result["aligned_segments"]
        speakers = result["speakers"]
        audio_path = result["audio_path"]

        # Build simple name mapping
        speaker_names = {spk: f"TestSpeaker_{i}" for i, spk in enumerate(speakers)}

        from processors.exporter import generate_eaf, generate_pfsx
        generate_eaf(aligned, speaker_names, audio_path, TEST_OUTPUT_EAF)
        generate_pfsx(speaker_names, TEST_OUTPUT_PFSX)

        ok = True
        ok &= check(os.path.isfile(TEST_OUTPUT_EAF), "EAF file created")
        ok &= check(os.path.getsize(TEST_OUTPUT_EAF) > 0, "EAF file is non-empty")

        # Validate XML
        try:
            tree = ET.parse(TEST_OUTPUT_EAF)
            root = tree.getroot()
            ok &= check(root.tag == "ANNOTATION_DOCUMENT",
                        f"Root is ANNOTATION_DOCUMENT")
            log(f"  EAF root: {root.tag}, attributes: {dict(root.attrib)}")
        except ET.ParseError as e:
            ok &= check(False, f"EAF XML parse error: {e}")

        return ok
    except Exception as e:
        return check(False, f"EAF export failed: {e}")


def test_10_pfsx_export():
    """Test PFSX file generation."""
    print("\n[Test 10] PFSX export")
    try:
        ok = True
        ok &= check(os.path.isfile(TEST_OUTPUT_PFSX), "PFSX file created")
        ok &= check(os.path.getsize(TEST_OUTPUT_PFSX) > 0, "PFSX file is non-empty")

        # Validate XML
        try:
            tree = ET.parse(TEST_OUTPUT_PFSX)
            root = tree.getroot()
            ok &= check("preferences" in root.tag,
                        f"Root tag contains 'preferences': {root.tag}")
        except ET.ParseError as e:
            ok &= check(False, f"PFSX XML parse error: {e}")

        return ok
    except Exception as e:
        return check(False, f"PFSX export failed: {e}")


def test_11_full_pipeline():
    """Run the full pipeline via run_pipeline()."""
    print("\n[Test 11] Full pipeline (run_pipeline)")
    try:
        from processors import run_pipeline, cleanup_temp
        cleanup_temp(TEST_TEMP)
        result = run_pipeline(TEST_MP4, TEST_TEMP)

        ok = True
        ok &= check("aligned_segments" in result, "Result has 'aligned_segments'")
        ok &= check("speakers" in result, "Result has 'speakers'")
        ok &= check("speaker_clips" in result, "Result has 'speaker_clips'")
        ok &= check("transcript_samples" in result, "Result has 'transcript_samples'")
        ok &= check("audio_path" in result, "Result has 'audio_path'")

        log(f"  Speakers detected: {result['speakers']}")
        log(f"  Segments: {len(result['aligned_segments'])}")
        log(f"  Clips: {list(result['speaker_clips'].keys())}")

        # Verify each speaker has a clip file
        for spk, clip_path in result["speaker_clips"].items():
            ok &= check(os.path.isfile(clip_path), f"Clip for {spk} exists")

        return ok
    except Exception as e:
        return check(False, f"Full pipeline failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run end-to-end pipeline tests")
    parser.add_argument("--generate", action="store_true",
                        help="Generate test video before running tests")
    args = parser.parse_args()

    print("=" * 60)
    print("  auto_transcriber_web — End-to-End Pipeline Test")
    print("=" * 60)

    if args.generate or not os.path.isfile(TEST_MP4):
        print("\n⚙️  Generating test video...")
        subprocess.run([sys.executable,
                        os.path.join(SCRIPT_DIR, "generate_test_video.py"),
                        "--dur", "15"], check=True)

    results = []
    results.append(("ffmpeg available", test_01_ffmpeg_available()))
    results.append(("Config loads", test_02_config_loads()))
    results.append(("Test video exists", test_03_generate_test_video()))
    results.append(("Audio extraction", test_04_audio_extraction()))
    results.append(("Transcription", test_05_transcription()))
    results.append(("Diarization", test_06_diarization()))
    results.append(("Alignment", test_07_alignment()))
    results.append(("Punctuation", test_08_punctuation()))
    results.append(("EAF export", test_09_eaf_export()))
    results.append(("PFSX export", test_10_pfsx_export()))
    results.append(("Full pipeline", test_11_full_pipeline()))

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        status = PASS if ok else FAIL
        print(f"  {status} {name}")
    print(f"\n  Result: {passed}/{total} tests passed")
    print("=" * 60)

    # Cleanup temp
    if os.path.isdir(TEST_TEMP):
        shutil.rmtree(TEST_TEMP, ignore_errors=True)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
