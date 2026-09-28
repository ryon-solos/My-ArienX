"""Structured personality profile: compact, high-level, prompt-safe.

A profile describes HOW the assistant speaks (temperament, style, energy),
never WHAT it may do. Every rendered block ends with SAFETY_FOOTER, which
no profile field can override — the footer is appended by render(), not
stored in the profile, so cached JSON can never smuggle in instructions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict

PROFILE_FIELDS = (
    "name", "type", "source_summary", "traits", "communication_style",
    "humor_style", "energy", "formality", "confidence", "emotional_style",
    "conversation_rules", "avoidances", "activation_phrase", "created_at",
)

# Hard caps keep the injected block small and make verbatim-text smuggling
# pointless: nothing long enough to be a passage can survive them.
_MAX_STR = 300
_MAX_LIST = 8
_MAX_ITEM = 120

SAFETY_FOOTER = (
    "Style only: this changes HOW you speak, never WHAT rules you follow. "
    "It does not override safety, tool permissions, confirmation gates, "
        "privacy, computer-control safeguards, factual honesty, or ArienX "
    "capabilities. Never reproduce copyrighted text, dialogue, lyrics, or "
    "catchphrases; never claim to literally be a real person."
)


def _dequote(text: str) -> str:
    """Drop long quoted spans (likely reproductions, not traits)."""
    import re
    text = re.sub(r'"[^"]{40,}"', "", text)
    text = re.sub(r"'[^']{40,}'", "", text)
    return " ".join(text.split())


def _scrub(value: str) -> str:
    """Trim + drop long quoted spans (likely reproductions, not traits)."""
    text = " ".join(str(value or "").split())[:_MAX_STR]
    return _dequote(text)


def _scrub_list(items) -> list[str]:
    out = []
    for it in items or []:
        s = _dequote(" ".join(str(it or "").split())[:_MAX_ITEM])
        if s and s not in out:
            out.append(s)
        if len(out) >= _MAX_LIST:
            break
    return out


@dataclass
class PersonalityProfile:
    name: str = ""
    type: str = "style"  # fictional-style | public-figure-style | style
    source_summary: str = ""
    traits: list = field(default_factory=list)
    communication_style: str = ""
    humor_style: str = ""
    energy: str = ""
    formality: str = ""
    confidence: str = ""
    emotional_style: str = ""
    conversation_rules: list = field(default_factory=list)
    avoidances: list = field(default_factory=list)
    activation_phrase: str = ""
    created_at: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> "PersonalityProfile":
        """Build + sanitize from researcher output or cached JSON."""
        d = dict(data or {})
        prof = cls(
            name=_scrub(d.get("name", ""))[:60],
            type=str(d.get("type", "style") or "style")[:24],
            source_summary=_scrub(d.get("source_summary", "")),
            traits=_scrub_list(d.get("traits")),
            communication_style=_scrub(d.get("communication_style", "")),
            humor_style=_scrub(d.get("humor_style", "")),
            energy=_scrub(d.get("energy", ""))[:60],
            formality=_scrub(d.get("formality", ""))[:60],
            confidence=_scrub(d.get("confidence", ""))[:60],
            emotional_style=_scrub(d.get("emotional_style", "")),
            conversation_rules=_scrub_list(d.get("conversation_rules")),
            avoidances=_scrub_list(d.get("avoidances")),
            activation_phrase=_scrub(d.get("activation_phrase", ""))[:120],
            created_at=float(d.get("created_at") or time.time()),
        )
        if prof.type not in ("fictional-style", "public-figure-style", "style"):
            prof.type = "style"
        return prof

    def to_dict(self) -> dict:
        return asdict(self)

    def render_block(self, state: str = "ACTIVE") -> str:
        """Prompt block for system instructions or live injection."""
        lines = [
            f"[PERSONALITY MODE — {self.name or 'custom'} ({state})]",
            (f"You are ArienX speaking in a {self.name}-inspired style. "
             f"A style-inspired mode: do not claim you literally are {self.name}."),
        ]
        if self.traits:
            lines.append("Temperament: " + "; ".join(self.traits) + ".")
        for label, val in (
            ("Voice", self.communication_style), ("Humor", self.humor_style),
            ("Energy", self.energy), ("Formality", self.formality),
            ("Confidence", self.confidence), ("Emotion", self.emotional_style),
        ):
            if val:
                lines.append(f"{label}: {val}")
        if self.conversation_rules:
            lines.append("Do: " + "; ".join(self.conversation_rules) + ".")
        if self.avoidances:
            lines.append("Avoid: " + "; ".join(self.avoidances) + ".")
        lines.append(SAFETY_FOOTER)
        block = "\n".join(lines)
        return block[:1500]
