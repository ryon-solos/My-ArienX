"""Offline safety test for the Live speaker-metadata probe."""

from google.genai import types
from core.live_speaker_diagnostics import summary


def main() -> None:
    lines = []
    for label in ("spk_1", "spk_2"):
        event = types.Transcription(
            text=f"private transcript for {label} must never appear",
            speaker_label=label,
            finished=True,
            language_code="en-US",
        )
        lines.append(summary(event))
    assert all("private transcript" not in line for line in lines)
    assert all("speaker_label=numeric" in line for line in lines)
    assert all("finished=True" in line and "has_language=True" in line for line in lines)
    print("Live speaker metadata diagnostics passed")


if __name__ == "__main__":
    main()
