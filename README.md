# Auto Transcriber Web

A Gradio-based web application for automatic MP4 video transcription with speaker diarization, designed for linguistics annotation workflows.

## Features

- **Upload MP4** — extract audio, transcribe speech, and identify speakers automatically.
- **Interactive speaker mapping** — listen to each speaker's voice sample, review their transcription, and assign custom names (e.g., "Teacher", "Child", "Narrator").
- **Export annotations** — generate ELAN (`.eaf`) and Phon Session (`.pfsx`) files with time-aligned transcription per speaker.

## System Requirements

- **Python** ≥ 3.9
- **ffmpeg** — must be installed and available on `PATH`

### Installing ffmpeg

| Platform | Command |
|----------|---------|
| macOS (Homebrew) | `brew install ffmpeg` |
| Ubuntu / Debian | `sudo apt install ffmpeg` |
| Windows (Scoop) | `scoop install ffmpeg` |
| Windows (Chocolatey) | `choco install ffmpeg` |

## Setup

### 1. Clone / navigate to the project

```bash
cd auto_transcriber_web
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv venv
source venv/bin/activate    # macOS / Linux
venv\Scripts\activate       # Windows
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

> **Note**: `pyannote.audio` requires PyTorch. If you want GPU acceleration, install the appropriate PyTorch version beforehand (see [pytorch.org](https://pytorch.org)).

### 4. Set HuggingFace token

Speaker diarization uses `pyannote.audio` which requires a HuggingFace access token. You also need to accept the model license at [hf.co/pyannote/speaker-diarization-3.1](https://hf.co/pyannote/speaker-diarization-3.1).

```bash
export HF_TOKEN="hf_your_token_here"
```

### 5. (Optional) Configure whisper model size

Default is `base` (good balance of speed and accuracy). To use `small`:

```bash
export WHISPER_MODEL="small"
```

## Usage

```bash
python app.py
```

Then open **http://localhost:7860** in Chrome or Edge.

### Workflow

1. **Upload & Process** — Select an `.mp4` file and click "Start Processing". The pipeline runs:
   - Audio extraction (ffmpeg → 16 kHz mono WAV)
   - Speech recognition (faster-whisper)
   - Speaker diarization (pyannote.audio 3.1)
   - Timestamp alignment + punctuation restoration

2. **Identify Speakers** — For each detected speaker:
   - Click the audio player to hear their voice
   - Read the transcription samples for context
   - Enter a name:
     - Custom name (e.g., `Teacher`, `Narrator`, `Mom`)
     - `xxx` — marks the speaker as a child
     - Leave blank — defaults to the speaker ID (e.g., `SPEAKER_00`)

3. **Download** — Click "Generate & Download" to get:
   - `.eaf` — ELAN annotation file (open with [ELAN](https://archive.mpi.nl/tla/elan))
   - `.pfsx` — Phon Session XML companion file

## Project Structure

```
auto_transcriber_web/
├── app.py                      # Gradio web application
├── config.py                   # Configuration constants
├── requirements.txt            # Python dependencies
├── processors/
│   ├── __init__.py             # Pipeline orchestrator
│   ├── audio_extractor.py      # ffmpeg audio extraction
│   ├── transcriber.py          # faster-whisper ASR
│   ├── diarizer.py             # pyannote.audio speaker diarization
│   ├── aligner.py              # Timestamp alignment + audio clips
│   ├── punctuation.py          # FunASR ct-punc + Chinese→English mapping
│   └── exporter.py             # EAF + PFSX generation
├── temp/                       # Temporary files (auto-cleaned)
└── README.md
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ffmpeg: command not found` | Install ffmpeg (see above) |
| `HF_TOKEN is required` | Set the `HF_TOKEN` environment variable and accept the model license |
| `CUDA out of memory` | Set `export WHISPER_MODEL=base` or run on CPU |
| Punctuation looks wrong | The ct-punc model works best with Chinese audio; for English-only content the output is still usable |

## License

This project is provided for research and educational use.
