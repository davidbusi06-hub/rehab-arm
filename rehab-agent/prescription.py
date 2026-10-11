"""A doctor's prescription or personal therapy plan, kept on file per patient.

The therapist uploads the document once (Personal plan tab). It is stored on
this machine only, under ``data/prescriptions/<patient>/``, which is
git-ignored: it is a medical document about a person. Its text is used to
prefill the Training plan tab's exercise recommender, which runs on the local
model; nothing here sends it anywhere.

One document per patient: uploading another replaces it.

    .txt / .md   read as text
    .pdf         text is extracted if ``pypdf`` is installed; otherwise the file
                 is still stored and the dashboard says its text is unavailable
"""

import io
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

PRESCRIPTION_DIR = Path(__file__).parent / "data" / "prescriptions"
ALLOWED_SUFFIXES = (".txt", ".md", ".pdf")
MAX_BYTES = 10 * 1024 * 1024  # matches maxUploadSize in .streamlit/config.toml


def _dir_for(patient_id):
    if not isinstance(patient_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", patient_id):
        raise ValueError(f"Unusable patient id for a prescription folder: {patient_id!r}")
    return PRESCRIPTION_DIR / patient_id


def extract_text(filename, data):
    """The document's text, or "" if it cannot be read as text here."""
    suffix = Path(filename).suffix.lower()
    if suffix in (".txt", ".md"):
        return data.decode("utf-8", errors="replace").strip()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        try:
            pages = PdfReader(io.BytesIO(data)).pages
            return "\n\n".join((page.extract_text() or "").strip() for page in pages).strip()
        except Exception:
            return ""
    return ""


def save(patient_id, filename, data):
    """Store a prescription for a patient, replacing any earlier one. Returns its record."""
    folder = _dir_for(patient_id)
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"Unsupported file type {suffix or '(none)'}; use one of {', '.join(ALLOWED_SUFFIXES)}.")
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")

    text = extract_text(name, data)
    staging = folder.with_name(folder.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    (staging / f"prescription{suffix}").write_bytes(data)
    (staging / "text.txt").write_text(text)
    meta = {
        "patient_id": patient_id,
        "filename": name,
        "stored_as": f"prescription{suffix}",
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "size_bytes": len(data),
        "text_chars": len(text),
    }
    (staging / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(staging, folder)
    return dict(meta, text=text)


def load(patient_id):
    """The stored prescription record with its text, or None if there is none."""
    folder = _dir_for(patient_id)
    try:
        meta = json.loads((folder / "meta.json").read_text())
        text = (folder / "text.txt").read_text()
    except (OSError, ValueError):
        return None
    return dict(meta, text=text)


def delete(patient_id):
    """Remove a patient's stored prescription. Returns True if there was one."""
    folder = _dir_for(patient_id)
    existed = folder.exists()
    shutil.rmtree(folder, ignore_errors=True)
    return existed
