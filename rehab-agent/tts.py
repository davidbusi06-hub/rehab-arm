"""Text-to-speech for the guided session, using ElevenLabs.

A CLOUD service: the text to be spoken is sent to ElevenLabs. Needs ELEVENLABS_API_KEY
(the same key the cloud transcription uses). The words themselves are composed by the
dashboard from the detected plan and the arm's own logs (arm_control.spoken_*); this
module only turns them into audio.
"""

import os

VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")   # "George", a stock voice
MODEL_ID = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")      # the low-latency model


def available():
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def speak(text):
    """Return MP3 bytes of `text` spoken aloud.

    Raises RuntimeError if no key is set; other failures (network, quota) propagate so
    the caller can fall back to showing the text only.
    """
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set, so replies are shown as text only.")
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=key)
    chunks = client.text_to_speech.convert(
        voice_id=VOICE_ID, model_id=MODEL_ID, text=text, output_format="mp3_44100_64"
    )
    return b"".join(chunks)


def explain(exc):
    """A short, key-free reason an ElevenLabs speech call failed, for the page."""
    body = str(getattr(exc, "body", "") or exc)
    if "missing the permission text_to_speech" in body:
        return ("The ElevenLabs key on this machine is not allowed to do text to speech. Its owner "
                "can switch that permission on under API Keys in the ElevenLabs settings.")
    if "quota" in body.lower():
        return "The ElevenLabs account has run out of credits."
    if isinstance(exc, RuntimeError):
        return str(exc)
    return "ElevenLabs speech is not reachable right now."
