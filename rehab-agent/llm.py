"""Local LLM access via Ollama (Person A).

Runs a local model on this AMD Ryzen AI Max+ 395 PC. No data leaves the
machine. This module only turns numbers from analysis.py into prose.

Talks to Ollama's HTTP API with the standard library only (no external
package required). Configure with environment variables:
    OLLAMA_HOST   base URL (default http://localhost:11434)
    OLLAMA_MODEL  model tag  (default llama3.1:8b)

Prereq: Ollama must be installed and running, and the model pulled:
    ollama pull llama3.1:8b
"""

import json
import os
import socket
import urllib.error
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
TIMEOUT_S = 120
# Low temperature: the model is wording fixed numbers, not inventing them.
TEMPERATURE = 0.2


def ask(system, user):
    """Send a system + user prompt to the local model and return its text.

    Args:
        system: system prompt (role / rules / grounding instructions).
        user: user prompt (the numbers and the question/task).

    Returns:
        str: the model's text response.

    Raises:
        RuntimeError: if the local Ollama server cannot be reached or the
            request fails, with a hint about the likely fix.
    """
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": TEMPERATURE},
    }
    req = urllib.request.Request(
        f"{HOST}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {HOST}. Is it running, and is "
            f"'{MODEL}' pulled? (ollama serve; ollama pull {MODEL})"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Ollama returned an unreadable response. Try generating again.") from exc
    try:
        content = body["message"]["content"].strip()
        if not content:
            raise ValueError("empty response")
        return content
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise RuntimeError("Ollama returned no usable feedback. Try generating again.") from exc
