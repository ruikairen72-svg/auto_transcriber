"""
Speaker diarization using pyannote.audio 3.1.
"""

from typing import Optional

from config import HF_TOKEN, MIN_DIARIZATION_SPEAKERS, MAX_DIARIZATION_SPEAKERS


def diarize(audio_path: str, hf_token: Optional[str] = None,
            num_speakers: Optional[int] = None) -> list[dict]:
    """
    Run speaker diarization on a WAV file.

    Parameters
    ----------
    audio_path : str
        Path to the 16 kHz mono WAV file.
    hf_token : str or None
        HuggingFace access token.  Falls back to the ``HF_TOKEN`` env var.
    num_speakers : int or None
        Expected number of speakers.  When provided, the diarization pipeline
        is constrained to this exact count.  When ``None``, the pipeline uses
        ``min_speakers=1``, ``max_speakers=6`` to reduce fragmentation.

    Returns
    -------
    list[dict]
        Each dict has ``start``, ``end`` (float seconds) and ``speaker``
        (e.g. ``"SPEAKER_00"``).
    """
    token = hf_token or HF_TOKEN
    if not token:
        raise RuntimeError(
            "HF_TOKEN is required for pyannote.audio speaker diarization. "
            "Set it as an environment variable or pass it explicitly."
        )

    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=token,
    )

    # pyannote returns None when the token is invalid or the user has not
    # accepted the model license — give a clear actionable error.
    if pipeline is None:
        raise RuntimeError(
            "pyannote pipeline failed to load.\n\n"
            "This usually means one of:\n"
            "  1. Your HF_TOKEN is invalid or expired.\n"
            "  2. You have not accepted the model license.\n\n"
            "Please visit https://huggingface.co/pyannote/speaker-diarization-3.1\n"
            "and click 'Agree and access repository', then verify your token at\n"
            "https://huggingface.co/settings/tokens"
        )

    # Run diarization with speaker-count guidance
    if num_speakers is not None:
        diarization = pipeline(audio_path, num_speakers=num_speakers)
    else:
        diarization = pipeline(
            audio_path,
            min_speakers=MIN_DIARIZATION_SPEAKERS,
            max_speakers=MAX_DIARIZATION_SPEAKERS,
        )

    results = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        results.append({
            "start": round(turn.start, 3),
            "end": round(turn.end, 3),
            "speaker": speaker,
        })

    return results
