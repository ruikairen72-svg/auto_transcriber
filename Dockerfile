# ============================================================
# Optional Dockerfile — for HF Spaces "Docker" SDK, or Koyeb / Render / Railway.
# Using it avoids pulling the multi-GB CUDA PyTorch wheels from PyPI.
# ============================================================
FROM python:3.10-slim

# ffmpeg (audio extraction subprocess) + libsndfile1 (soundfile/librosa)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Install CPU-only torch/torchaudio FIRST so that
# `pip install -r requirements.txt` sees them satisfied and skips them.
COPY requirements.txt ./
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_PORT=7860

EXPOSE 7860

CMD ["python", "app.py"]
