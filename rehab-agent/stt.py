"""Speech-to-text (Person A), local-first with an optional cloud backend.

Two backends:
  * "local"       -- faster-whisper on this device. Audio never leaves the
                     machine (same local/private principle as the LLM). Default.
  * "elevenlabs"  -- ElevenLabs Scribe, more accurate but a CLOUD service: the
                     audio is sent to ElevenLabs. Needs ELEVENLABS_API_KEY.

Pick per call with transcribe(..., backend=...) or via the STT_BACKEND env var.
Audio is decoded with soundfile (avoids the PyAV clash in this environment).
"""

import io
import os

import numpy as np
import soundfile as sf
from scipy.signal import resample

LOCAL_MODEL_SIZE = "base.en"
ELEVEN_MODEL = "scribe_v1"
_LOCAL_MODEL = None


def _default_backend():
    return os.environ.get("STT_BACKEND", "local").lower()


def transcribe(audio_bytes, backend=None):
    """Transcribe audio bytes (WAV/FLAC) to text.

    Args:
        audio_bytes: raw audio file bytes (e.g. from st.audio_input()).
        backend: "local" or "elevenlabs"; defaults to $STT_BACKEND or "local".

    Returns:
        str: recognized text, or "" if there is no usable audio.

    Raises:
        RuntimeError: ElevenLabs selected without ELEVENLABS_API_KEY set.
    """
    if not audio_bytes:
        return ""
    backend = (backend or _default_backend()).lower()
    if backend == "elevenlabs":
        return _transcribe_elevenlabs(audio_bytes)
    return _transcribe_local(audio_bytes)


def _transcribe_local(audio_bytes):
    from faster_whisper import WhisperModel

    global _LOCAL_MODEL
    data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.size == 0:
        return ""
    if sr != 16000:
        data = resample(data, int(len(data) * 16000 / sr)).astype("float32")
    if _LOCAL_MODEL is None:
        _LOCAL_MODEL = WhisperModel(LOCAL_MODEL_SIZE, device="cpu", compute_type="int8")
    segments, _ = _LOCAL_MODEL.transcribe(data, language="en")
    return " ".join(s.text for s in segments).strip()


def _transcribe_elevenlabs(audio_bytes):
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is not set. Set it to use ElevenLabs "
            "transcription, or switch the backend to local."
        )
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=key)
    resp = client.speech_to_text.convert(
        model_id=ELEVEN_MODEL, file=io.BytesIO(audio_bytes)
    )
    return (getattr(resp, "text", "") or "").strip()
