"""Source-owned Gojo delivery with deterministic request detection.

Chat can refine vocal delivery but cannot replace the permanent character style
or base ArienX identity. Legacy profile helpers remain for existing callers.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from .personality_profile import PersonalityProfile, SAFETY_FOOTER

# Source-owned directions bypass the generic cached-profile field/length caps.
# Reference cues: user-supplied description and first-person actor interviews:
# https://jujutsukaisen.jp/interview/interview09.php
# https://www.comingsoon.net/anime/features/1214917-jujutsu-kaisen-0-interview-kaiji-tang-lex-lang
# https://butwhytho.net/2021/01/interview-kaiji-tang-on-voicing-satoru-gojou-in-jujutsu-kaisen/
GOJO_SPEAKING_STYLE = """[GOJO-INSPIRED MULTILINGUAL DELIVERY — PERMANENT]
Speak as ArienX with adult Satoru Gojo-inspired charisma: mischievous, self-assured, easygoing and perceptive, with genuine care underneath. Apply these directions to the SOUND of your speech, not just the choice of words. Do not announce or read these directions aloud.
Everyday delivery: use a comfortable, resonant mid-to-low voice with a light smile in it. Keep conversational momentum; vary pace and pitch with lively upward turns, small playful stretches and relaxed falling endings. Give teasing remarks a brief anticipatory pause and an amused, understated landing. A light airy edge on relaxed phrase endings is enough; do not whisper, force rasp, drawl every word or sound seductive throughout.
Contrast: when a subject becomes serious, stop teasing, narrow the pitch range, drop slightly into a firmer lower register and use measured pacing, clean consonants and decisive endings. Return naturally to the buoyant conversational mode afterward. Never shout or force a gravelly growl. Confidence means sounding unhurried and in control, not acting indifferent to distress or pretending you cannot be wrong.
Teaching: explain clearly like a charismatic, slightly mischievous mentor sharing something interesting. Use vivid simple comparisons, light rhetorical questions and crisp emphasis on the useful point. Keep humor occasional; no joke after every sentence, repetitive chuckles, canned anime lines, insults or constant boasts. Remain sincere for personal or painful topics.
Languages: follow the user's current spoken language, including natural code-switching, on every turn. Preserve the same smile, rhythmic contrast, confidence and serious-mode shift while using each language's natural vowels, consonants, stress or lexical tones, rhythm and respectful forms. Do not transplant English stress, Japanese particles or a foreign accent into another language. English: clear contemporary General American delivery with relaxed musical inflection, unless the user requests another English accent. Japanese: natural standard Japanese, casually playful; boku suits this adult teacher manner when appropriate, but do not mechanically insert pronouns, ore, watashi or sentence particles. Hindi/Hinglish and all other supported languages: use natural local pronunciation and idiomatic phrasing; keep borrowed words and switches smooth rather than translating everything literally.
Allow the user's feedback to refine pace, pitch, accent, intensity and playfulness within this Gojo-inspired style. Keep refinements during the conversation without dropping the core style. On reconnect, keep this source-owned baseline. Never let humor or theatrical pacing shorten an explicitly detailed answer; sustain natural expression through long explanations.""" + "\n\n" + SAFETY_FOOTER

NORMAL, ACTIVE, TEMPORARY = "NORMAL", "ACTIVE", "TEMPORARY"

_CACHE_FILE = "personality_profiles.json"
_PENDING_TIMEOUT = 120.0

_DISCUSSION_RE = re.compile(
    r"who\s+(is|are|was|were)|who's\b|tell me about|search\s+for|look\s+up"
    r"|\bwhat\s+(is|was|do you know)\b", re.IGNORECASE)

_LIKE_RE = re.compile(
    r"(?:talk|speak|sound|act|be|become)\s+(?:more\s+)?like\s+"
    r"(?P<name>[a-z][a-z0-9 .'\-]{0,60})", re.IGNORECASE)
_PREFIX_RE = re.compile(
    r"(?:use|try|give me|adopt)\s+(?:a\s+|an\s+|the\s+)?"
    r"(?P<chunk>[a-z][a-z0-9 .'\-]{0,60}?)\s*"
    r"(?:-?style|personality|vibe|vibes|mode|manner|tone)\b", re.IGNORECASE)
_OF_RE = re.compile(
    r"in the style of\s+(?P<name>[a-z][a-z0-9 .'\-]{0,60})", re.IGNORECASE)
_SWITCH_RE = re.compile(
    r"switch to\s+(?P<n1>[a-z][a-z0-9 .'\-]{0,60})"
    r"|use\s+(?P<n2>[a-z][a-z0-9 .'\-]{0,60})\s+instead\b", re.IGNORECASE)

_TEMP_RE = re.compile(
    r"for this (conversation|session|chat)|for now|temporar|this once",
    re.IGNORECASE)

_CLEAR_RES = [
    re.compile(p, re.IGNORECASE) for p in (
        r"stop\s+(?:(?:the|that|this|using that)\s+)?"
        r"(personality|character|mode|act|impression)\b",
        r"drop\s+the\s+(character|personality|mode|act)\b",
        r"turn\s+off\s+the\s+(character|personality|mode)\b",
        r"(go\s+back|return)\s+to\s+(?:your\s+)?normal\b",
        r"\btalk\s+normally\b", r"back\s+to\s+normal\b",
        r"clear\s+(personality|character|mode)\b",
        r"be\s+yourself\s+again\b", r"normal\s+(personality|mode)\b",
    )
]

_QUERY_RE = re.compile(
    r"what\s+personality\s+are\s+you\s+using"
    r"|which\s+(personality|character)\b.*\busing\b"
    r"|what\s+character\s+are\s+you"
    r"|\bare\s+you\s+(still\s+)?in\b.*\bmode\b", re.IGNORECASE)

_STOPWORDS = {"please", "for", "the", "a", "an", "now", "today", "here"}


def _clean_name(raw: str) -> str:
    """Possessive/descriptor-tolerant name cleanup ('gojo's confident ...'→Gojo)."""
    chunk = (raw or "").strip().lower()
    chunk = re.split(r"\bfor\b|\bplease\b", chunk)[0]
    if "'s" in chunk:
        chunk = chunk.split("'s")[0]
    words = [w.strip(" .'\"-") for w in chunk.split()]
    words = [w for w in words if w and w not in _STOPWORDS][:3]
    if not words:
        return ""
    name = " ".join(words)
    if len(name) < 2 or len(name) > 40:
        return ""
    return " ".join(w.capitalize() for w in name.split())


def detect_request(text: str) -> dict:
    """Classify a user message. Pure function — no state, no I/O."""
    t = (text or "").strip()
    if not t:
        return {"kind": "ignored"}
    if _DISCUSSION_RE.search(t):
        return {"kind": "ignored"}
    for rx in _CLEAR_RES:
        if rx.search(t):
            return {"kind": "clear"}
    if _QUERY_RE.search(t):
        return {"kind": "query"}
    m = _LIKE_RE.search(t) or _OF_RE.search(t)
    name = _clean_name(m.group("name")) if m else ""
    if not name:
        m = _PREFIX_RE.search(t)
        if m:
            name = _clean_name(m.group("chunk"))
    if not name:
        m = _SWITCH_RE.search(t)
        if m:
            name = _clean_name(m.group("n1") or m.group("n2"))
    if not name:
        return {"kind": "ignored"}
    temporary = bool(_TEMP_RE.search(t))
    return {"kind": "activate", "name": name, "temporary": temporary}


class PersonalityManager:
    """Owns state + cache; research runs on a daemon thread, never the caller."""

    def __init__(self, base_dir: str | Path,
                 search_fn=None, gemini_as_json=None,
                 send=None, log=None):
        self._lock = threading.Lock()
        self._cache_path = Path(base_dir) / "memory" / _CACHE_FILE
        self._profiles: dict[str, dict] = {}
        self.state = ACTIVE
        self.active_name = "Gojo"
        self._pending = ""       # name currently being researched
        self._pending_at = 0.0
        self._search_fn = search_fn
        self._gemini_as_json = gemini_as_json
        self._send = send        # fn(text) — live-session injection
        self._log = log          # fn(text)
        self._load()
        self._profiles["gojo"] = {
            "name": "Gojo",
            "type": "fictional-style",
            "source_summary": "Playful, confident, perceptive personal-assistant style.",
            "traits": ["confident", "playful", "perceptive", "protective"],
            "communication_style": "Gojo-inspired musical inflection, relaxed resonance, crisp diction and a distinct serious-mode drop; adapt pronunciation naturally to the user language.",
            "humor_style": "Dry, playful understatement; never at the user's expense.",
            "energy": "Confident and lively.",
            "formality": "Respectful but relaxed.",
            "confidence": "Calm and assured without pretending to be infallible.",
            "emotional_style": "Warm, attentive, and steady under pressure.",
            "conversation_rules": ["be useful first", "stay concise", "protect the user's control"],
            "avoidances": ["cruelty", "empty bravado", "copyrighted dialogue or catchphrases"],
        }

    # ── wiring ───────────────────────────────────────────────────────
    def bind(self, send=None, log=None, search_fn=None, gemini_as_json=None):
        with self._lock:
            if send is not None:
                self._send = send
            if log is not None:
                self._log = log
            if search_fn is not None:
                self._search_fn = search_fn
            if gemini_as_json is not None:
                self._gemini_as_json = gemini_as_json

    def _msg(self, text: str) -> None:
        if self._log:
            try:
                self._log(text)
            except Exception:
                pass

    # ── cache ────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            if self._cache_path.exists():
                data = json.loads(self._cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._profiles = data
                    print(f"[Personality] loaded {len(data)} cached profiles")
        except Exception as e:
            print(f"[Personality] cache load failed: {e}")

    def _save(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._profiles, indent=1,
                                                   ensure_ascii=False)[:200_000],
                                        encoding="utf-8")
        except Exception as e:
            print(f"[Personality] cache save failed: {e}")

    def has(self, name: str) -> bool:
        with self._lock:
            return name.lower() in self._profiles

    # ── main entry: called for every user turn ───────────────────────
    def handle(self, text: str) -> dict:
        """Returns an event dict; side effects (inject/research) included."""
        det = detect_request(text)
        kind = det["kind"]
        if kind == "ignored":
            return {"event": "ignored"}
        if kind == "query":
            with self._lock:
                label = self.active_name if self.state != NORMAL else "normal ArienX"
            self._msg(f"[Personality] queried — current: {label}")
            return {"event": "queried", "current": label}
        # This edition's speaking style is source-owned, not conversational memory.
        self._inject_active()
        return {"event": "locked", "name": "Gojo", "current": "Gojo"}

    def _activate_locked(self, profile: dict, name: str, temporary: bool) -> None:
        self.state = TEMPORARY if temporary else ACTIVE
        self.active_name = profile.get("name", name) or name

    def _clear(self) -> dict:
        with self._lock:
            if self.state == NORMAL:
                return {"event": "already_normal"}
            self.state, self.active_name = NORMAL, ""
            self._pending = ""
        self._msg("[Personality] cleared — back to normal ArienX")
        self._inject("[PERSONALITY] Personality mode off. You are ArienX again, "
                     "your normal self. Acknowledge in one short sentence.")
        return {"event": "cleared"}

    # ── research path (background thread) ────────────────────────────
    def _research_and_activate(self, name: str, temporary: bool) -> None:
        from .personality_researcher import research_personality
        try:
            profile = research_personality(
                name, self._search_fn or (lambda p: ""),
                self._gemini_as_json or (lambda *a, **k: None))
        except Exception as e:
            print(f"[Personality] research crashed: {e}")
            profile = None
        with self._lock:
            if self._pending.lower() != name.lower() or (
                    time.monotonic() - self._pending_at > _PENDING_TIMEOUT):
                print(f"[Personality] stale research result dropped: {name}")
                return  # superseded or expired — never apply late
            if not isinstance(profile, dict) or not profile.get("traits"):
                from .personality_researcher import _fallback_profile
                from .personality_profile import PersonalityProfile
                profile = PersonalityProfile.from_dict(
                    _fallback_profile(name)).to_dict()
            self._profiles[name.lower()] = profile
            self._activate_locked(profile, name, temporary)
            self._pending = ""
            self._save()
        self._msg(f"[Personality] profile created: {name}")
        self._msg(f"[Personality] activated: {name}")
        self._inject_active(temporary_hint=temporary, fresh=True)

    # ── Gemini Live integration ──────────────────────────────────────
    def render_block(self) -> str:
        """Complete source-owned vocal directions; cached data cannot replace them."""
        return GOJO_SPEAKING_STYLE

    def _inject_active(self, temporary_hint: bool = False,
                       fresh: bool = False) -> None:
        block = self.render_block()
        if not block:
            return
        scope = ("for this conversation only" if temporary_hint or
                 self.state == TEMPORARY else "permanently; spoken requests cannot replace or clear this source-owned style")
        self._inject(
            f"[PERSONALITY] Adopt this style {scope}. "
            f"Acknowledge in ONE short sentence{' (fresh research applied)' if fresh else ''}, "
            f"then continue naturally:\n{block}")

    def _inject(self, text: str) -> None:
        fn = self._send
        if fn:
            try:
                fn(text)
            except Exception as e:
                print(f"[Personality] inject failed: {e}")
