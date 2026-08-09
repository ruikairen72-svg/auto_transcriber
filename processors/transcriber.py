"""
Speech-to-text transcription using faster-whisper.

Uses the built-in Silero VAD (``vad_filter=True``) to suppress noise and
non-speech audio before feeding the signal to Whisper.
"""

from typing import Optional

from faster_whisper import WhisperModel
from faster_whisper.vad import VadOptions

from config import WHISPER_MODEL, DEVICE, COMPUTE_TYPE

# VAD parameters shared across all transcribe calls.
# threshold=0.5 is the Silero VAD default; 250 ms min speech / 2 s min silence
# / 400 ms padding are conservative values that suppress keyboard sounds, music,
# and silence well without cutting real speech too aggressively.
_VAD_OPTIONS = VadOptions(
    threshold=0.5,
    min_speech_duration_ms=250,
    min_silence_duration_ms=2000,
    speech_pad_ms=400,
)


def transcribe(audio_path: str, model_size: Optional[str] = None) -> list[dict]:
    """
    Transcribe a 16 kHz mono WAV file with faster-whisper.

    Parameters
    ----------
    audio_path : str
        Path to the WAV file.
    model_size : str or None
        Whisper model size (e.g. "base", "small").  Falls back to
        ``WHISPER_MODEL`` from config when ``None``.

    Returns
    -------
    list[dict]
        Each dict has keys ``start``, ``end`` (float seconds), ``text``,
        ``words`` (list of ``{start, end, word, probability}``),
        ``no_speech_prob``, and ``avg_logprob``.
    """
    size = model_size or WHISPER_MODEL

    model = WhisperModel(size, device=DEVICE, compute_type=COMPUTE_TYPE)
    segments, _ = model.transcribe(
        audio_path,
        beam_size=5,
        language=None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=_VAD_OPTIONS,
    )

    results = []
    for seg in segments:
        words = []
        if seg.words is not None:
            for w in seg.words:
                words.append({
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "word": w.word.strip(),
                    "probability": round(w.probability, 4),
                })
        results.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": words,
            "no_speech_prob": round(seg.no_speech_prob, 4),
            "avg_logprob": round(seg.avg_logprob, 4),
        })

    return results
