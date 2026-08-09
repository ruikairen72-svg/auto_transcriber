"""
Central configuration for auto_transcriber_web.

Reads ``HF_TOKEN`` from environment or a local ``.env`` file.
"""

import functools
import os
import sys
import torch

# PyTorch >= 2.6 changed ``torch.load`` to ``weights_only=True`` by default.
# pyannote.audio and FunASR checkpoints contain custom classes that are not in
# the default safe-globals list.  Patch ``torch.load`` globally so it defaults
# to ``weights_only=False`` — the behaviour these libraries were built against.
_original_torch_load = torch.load

@functools.wraps(_original_torch_load)
def _patched_torch_load(*args, **kwargs):
    # Force weights_only=False even when callers explicitly pass True.
    # Older libraries (pyannote, FunASR, speechbrain) ship checkpoints
    # with custom globals that aren't in the PyTorch 2.6+ safe list.
    kwargs["weights_only"] = False
    return _original_torch_load(*args, **kwargs)

torch.load = _patched_torch_load


# ---------------------------------------------------------------------------
# Load .env file if present (simple parser — no python-dotenv dependency)
# ---------------------------------------------------------------------------
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_ENV_FILE):
    with open(_ENV_FILE, "r", encoding="utf-8") as _fh:
        for _line in _fh:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            _key = _key.strip()
            _val = _val.strip().strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_FORMAT = "wav"

# ---------------------------------------------------------------------------
# Model settings
# ---------------------------------------------------------------------------
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")

# Device auto-detection
if torch.cuda.is_available():
    DEVICE = "cuda"
    COMPUTE_TYPE = "float16"
else:
    DEVICE = "cpu"
    COMPUTE_TYPE = "int8"

# ---------------------------------------------------------------------------
# HuggingFace token (required for pyannote.audio)
# ---------------------------------------------------------------------------
HF_TOKEN = os.environ.get("HF_TOKEN", "")


def validate_token() -> str:
    """
    Return the HF_TOKEN if set, otherwise print a friendly error and exit.

    Call this early in ``app.py`` before the UI is built so the user sees a
    clear message instead of a cryptic 401 later.
    """
    token = HF_TOKEN
    if not token:
        print("=" * 72)
        print("❌  HF_TOKEN is not set.")
        print()
        print("   pyannote.audio (speaker diarization) requires a HuggingFace token.")
        print()
        print("   To fix this:")
        print("   1. Get a token at https://huggingface.co/settings/tokens")
        print("   2. Accept the model license at")
        print("      https://huggingface.co/pyannote/speaker-diarization-3.1")
        print("   3. Set the token:")
        print("        export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx")
        print("      or create a .env file in auto_transcriber_web/ with:")
        print("        HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx")
        print("=" * 72)
        sys.exit(1)
    return token


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
MAX_SPEAKERS = 10
MIN_DIARIZATION_SPEAKERS = 1
MAX_DIARIZATION_SPEAKERS = 6
CLIP_DURATION = 5.0  # seconds for representative speaker audio clip

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")

# ---------------------------------------------------------------------------
# Punctuation mapping — Chinese codepoints → English equivalents
# ---------------------------------------------------------------------------
PUNCTUATION_MAP = {
    "。": ".",   # 。
    "，": ",",   # ，
    "？": "?",   # ？
    "！": "!",   # ！
    "；": ";",   # ；
    "：": ":",   # ：
    "、": ",",   # 、
    "“": '"',   # "
    "”": '"',   # "
    "（": "(",   # （
    "）": ")",   # ）
}

# Characters that indicate a sentence boundary (for capitalisation)
SENTENCE_TERMINATORS = {".", "?", "!"}
