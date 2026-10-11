"""Text-to-speech for the guided session, using ElevenLabs.

A CLOUD service: the text to be spoken is sent to ElevenLabs. Needs ELEVENLABS_API_KEY
(the same key the cloud transcription uses). The words themselves are composed by the
dashboard from the detected plan and the arm's own logs (arm_control.spoken_*); this
module only turns them into audio.
"""

import os

# "Daniel", a stock British male voice: calm and formal, in the style of a film AI butler.
# A stock voice on purpose; this does not clone or imitate any real person's voice.
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")
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


# Browser fallback, used when ElevenLabs is refused or unreachable: the first of these
# British male voices the browser has, spoken a little slower and lower than its default.
BROWSER_VOICES = ["Google UK English Male", "Daniel", "Microsoft Ryan", "Microsoft George", "Arthur", "Oliver"]


def browser_speech_js(text):
    """A <script> that speaks `text` with the browser's own British voice, if it has one."""
    import json

    return (
        "<script>(function(){try{"
        "const s=(window.parent||window).speechSynthesis;"
        f"const want={json.dumps(BROWSER_VOICES)};const text={json.dumps(text)};"
        "let done=false;"
        "function say(){if(done)return;const vs=s.getVoices();if(!vs.length)return;done=true;"
        "let v=null;for(const w of want){v=vs.find(x=>x.name.indexOf(w)===0);if(v)break;}"
        "if(!v)v=vs.find(x=>x.lang==='en-GB');"
        "const u=new SpeechSynthesisUtterance(text);if(v){u.voice=v;u.lang=v.lang;}"
        "u.rate=0.95;u.pitch=0.85;s.cancel();s.speak(u);}"
        "say();if(!done){s.onvoiceschanged=say;setTimeout(function(){"
        "if(!done){done=true;s.cancel();s.speak(new SpeechSynthesisUtterance(text));}},1500);}"
        "}catch(e){}})();</script>"
    )
