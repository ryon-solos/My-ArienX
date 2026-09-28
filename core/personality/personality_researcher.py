"""Turn web research into a structured, high-level personality profile.

Pipeline: 2 bounded web_search calls (existing infrastructure, no new deps)
→ one strict JSON extraction via core.gemini → sanitize via
PersonalityProfile.from_dict → fallback archetype on any failure.

The extraction prompt forbids quotations, catchphrases, plot recaps, and
passages; the researcher returns structured data, never research text, so
raw page content can never reach the prompt.
"""

from __future__ import annotations

import time

EXTRACTION_PROMPT = """Summarize the personality below as HIGH-LEVEL traits only.
STRICT RULES: no quotations, no catchphrases, no dialogue, no lyrics, no plot
recaps, no passages from any source. Short abstract phrases only.
Reply with JSON ONLY, exactly these keys:
{"name": "...", "type": "fictional-style|public-figure-style|style",
"source_summary": "<=25 words: who/what this is, factually",
"traits": ["<=6 short temperament words"],
"communication_style": "<=25 words", "humor_style": "<=20 words",
"energy": "<=10 words", "formality": "<=10 words", "confidence": "<=10 words",
"emotional_style": "<=20 words",
"conversation_rules": ["<=4 short DO rules"],
"avoidances": ["<=4 short AVOID rules, incl. never quoting the source"]}
Character: __PNAME__
Research notes (do NOT copy; abstract only):
__PNOTES__"""


def _fallback_profile(name: str) -> dict:
    """Safe generic archetype when research or extraction fails."""
    return {
        "name": name,
        "type": "style",
        "source_summary": "Fallback profile: no reliable research available.",
        "traits": ["expressive", "distinctive", "engaging"],
        "communication_style": "Lively, colorful delivery with strong presence.",
        "humor_style": "Light playful remarks, never at the user's expense.",
        "energy": "high",
        "formality": "casual",
        "confidence": "assured",
        "emotional_style": "Warm and upbeat, steady under bad news.",
        "conversation_rules": ["Stay in character voice, briefly",
                               "Keep answers useful first, style second"],
        "avoidances": ["Quoting any source text", "Claiming to be a real person"],
        "activation_phrase": name,
        "created_at": time.time(),
    }


def research_personality(name: str, search_fn, gemini_as_json,
                         timeout_ms: int = 30_000, max_chars: int = 3000) -> dict:
    """Build a profile dict for `name`. Never raises; falls back."""
    from .personality_profile import PersonalityProfile

    notes = ""
    try:
        r1 = search_fn({"query": f"{name} character personality traits "
                                 f"communication style",
                        "mode": "search"}) or ""
        r2 = search_fn({"query": f"{name} personality analysis",
                        "mode": "research"}) or ""
        notes = f"{r1}\n{r2}"[:max_chars]
    except Exception as e:
        print(f"[Personality] research search failed: {e}")

    if notes.strip():
        try:
            prompt = (EXTRACTION_PROMPT
                      .replace("__PNAME__", name)
                      .replace("__PNOTES__", notes))
            data = gemini_as_json(prompt, "SMART", None, timeout_ms, "", None)
            if isinstance(data, dict) and data.get("traits"):
                data["name"] = name
                data["created_at"] = time.time()
                return PersonalityProfile.from_dict(data).to_dict()
            print("[Personality] extraction returned nothing usable — fallback")
        except Exception as e:
            print(f"[Personality] extraction failed: {e}")
    else:
        print("[Personality] no research notes — fallback")
    return PersonalityProfile.from_dict(_fallback_profile(name)).to_dict()
