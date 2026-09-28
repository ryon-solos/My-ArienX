"""Opt-in, metadata-only inspection for Gemini Live transcription events.

This is deliberately not speaker recognition.  It records no audio, transcript
text, word values, names, or identifiers beyond Gemini's transient label shape.
Set ``ARIENX_SPEAKER_METADATA_DIAGNOSTICS=1`` for a live compatibility probe.
"""

from __future__ import annotations

import os


def enabled() -> bool:
    return os.environ.get("ARIENX_SPEAKER_METADATA_DIAGNOSTICS") == "1"


def summary(transcription) -> str:
    """Return only schema/availability metadata, never transcript content."""
    fields = getattr(transcription, "model_fields_set", set()) or set()
    label = getattr(transcription, "speaker_label", None)
    label_shape = "none"
    if label:
        normal = str(label).lower().replace("_", " ").replace("-", " ")
        label_shape = "numeric" if normal.startswith(("spk ", "speaker ")) else "other"
    words = getattr(transcription, "words", None)
    return (
        "[SpeakerMeta] input_transcription "
        f"fields={','.join(sorted(map(str, fields))) or 'none'} "
        f"speaker_label={label_shape} finished={bool(getattr(transcription, 'finished', False))} "
        f"has_language={bool(getattr(transcription, 'language_code', None))} "
        f"word_count={len(words or [])}"
    )
