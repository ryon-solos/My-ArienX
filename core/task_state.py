"""
core/task_state.py — Phase F: minimal multi-action task state.

Passive tracker, not an orchestrator. The model still chains tools itself
(one tool per turn, as before); this module only remembers what the chain
looks like so every tool result can carry a short ACTION → VERIFY →
CONTINUE / RECOVER / FAIL hint.

State kept (exactly the Phase F minimum):
  original_request — the user's words that started the task
  current step     — count of chaining tool calls so far
  last action/result — tool name + short arg summary + result summary
  failure / cancellation state — status + failure text + cancel reason

Thread-safe: _note_user_request runs on the Qt thread (typed commands) and
in the async receive loop (voice transcripts), while _execute_tool runs in
the session task. A single Lock guards all transitions.

Confirmation gates (core/confirm.py) are untouched: a [CONFIRMATION_PENDING]
result parks the task in AWAITING_CONFIRMATION instead of counting as a
failure, and the next user turn resumes it.
"""

from __future__ import annotations

import re
import threading
import time

# Tools whose calls count as chain steps. Memory reads/writes, vision,
# status checks and the monitor have no place in a TASK hint — attaching
# one would only add noise to turns that were never part of a task.
CHAIN_TOOLS = frozenset({
    "open_app",
    "browser_control",
    "computer_control",
    "computer_settings",
    "desktop_control",
    "file_controller",
    "file_processor",
    "send_message",
    "code_helper",
    "youtube_video",
    "reminder",
    "flight_finder",
    "game_updater",
    "weather_report",
    "web_search",
})

# Status values. IDLE = nothing yet; ACTIVE = chain in flight;
# AWAITING_CONFIRMATION = parked behind the on-screen gate (not a failure);
# DONE / FAILED / CANCELLED are terminal until the next user request.
IDLE = "idle"
ACTIVE = "active"
AWAITING_CONFIRMATION = "awaiting_confirmation"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
_TERMINAL = (DONE, FAILED, CANCELLED)

# A cancel is only recognised while a task is ACTIVE, so "stop camera" or
# "stop the music" outside a chain can never kill anything.
_CANCEL_PHRASES = (
    "cancel", "abort", "never mind", "nevermind", "forget it", "forget that",
    "stop that", "stop it", "stop this", "cancel that", "cancel it",
    "cancel this", "don't do it", "dont do it", "don't do that",
    "dont do that", "do not do that",
    "iptal", "vazgec", "vazgeç", "boşver", "bosver",
)
# Bare "stop" / "dur" only count when that is (almost) the whole message,
# otherwise "stop camera" would cancel a chain by accident.
_CANCEL_SHORT = ("stop", "dur", "dur dur", "stop stop")

_FAIL_MARKERS = (
    "failed", "failure", "could not", "couldn't", "unable to",
    "not found", "unknown tool", "timed out", "timed-out",
    "error", "no results", "nothing found",
)

_CONFIRM_TOKEN = "[CONFIRMATION_PENDING]"
_CANCELLED_TOKEN = "[TASK_CANCELLED]"
_DONE_TOKEN = "[TASK_DONE]"

# Follow-ups that CONTINUE the current site/task instead of starting over:
# leading discourse markers ("now ...", "then ...") or references to what is
# already open ("it", "that", "there", "the first result", ...). Checked only
# while a task is ACTIVE; anything else starts (or supersedes) as before.
_CONTINUATION_LEADS = ("now ", "then ", "also ", "and then ", "and ")
_REFERENCE_WORDS = (
    " it", " it.", " it,", "that ", "those ", "this ", "these ", "there",
    "same ", "first result", "next ", "previous ", "another ", "more ",
    "them", "play it", "open it", "search there",
)

# Host/app fragments mapped to a short site label for hints. Unknown hosts
# fall back to the raw hostname so the hint still names somewhere concrete.
_KNOWN_SITES = (
    ("youtube", "YouTube"), ("youtu.be", "YouTube"),
    ("mail.google", "Gmail"), ("gmail", "Gmail"),
    ("google.", "Google"), ("bing.", "Bing"), ("duckduckgo", "DuckDuckGo"),
    ("github", "GitHub"), ("stackoverflow", "StackOverflow"),
    ("spotify", "Spotify"), ("whatsapp", "WhatsApp"), ("telegram", "Telegram"),
    ("discord", "Discord"), ("netflix", "Netflix"), ("twitch", "Twitch"),
    ("reddit", "Reddit"), ("x.com", "X"), ("twitter", "Twitter"),
    ("facebook", "Facebook"), ("instagram", "Instagram"),
    ("chrome", "Chrome"), ("firefox", "Firefox"),
    ("brave", "Brave"), ("opera", "Opera"), ("safari", "Safari"),
    ("vscode", "VSCode"),
)


def _extract_site(tool: str, args: dict | None) -> str:
    """Best-effort 'where are we' label from a chaining tool call.

    Hosts match by substring ("mail.google.com" → Gmail); bare words match
    whole-word only, so "knowledge" never becomes Edge and "cooperation"
    never becomes Opera.
    """
    try:
        args = args or {}
        if (tool or "") == "youtube_video":
            return "YouTube"
        for key in ("url", "query", "text", "description", "app", "app_name",
                    "target", "browser"):
            val = str(args.get(key) or "").lower()
            if not val:
                continue
            m = re.search(r"(?:https?://)?([a-z0-9.-]+\.[a-z]{2,})", val)
            if m:
                host = m.group(1)
                for frag, label in _KNOWN_SITES:
                    if frag in host:
                        return label
                return host
            words = set(re.findall(r"[a-z0-9]+", val))
            for frag, label in _KNOWN_SITES:
                if frag in words:
                    return label
        b = str(args.get("browser") or "").strip()
        if b:
            return b.capitalize()
    except Exception:
        pass
    return ""


def is_continuation(text: str) -> bool:
    """True if this message reads as a follow-up to the current task."""
    t = (text or "").strip().lower()
    if not t or len(t) > 160:
        return False
    if t.startswith(_CONTINUATION_LEADS):
        return True
    padded = f" {t} "
    return any(w in padded for w in _REFERENCE_WORDS)


def is_cancel_request(text: str) -> bool:
    """True if this user message reads as cancelling the current task."""
    t = (text or "").strip().lower()
    if not t:
        return False
    for phrase in _CANCEL_PHRASES:
        if phrase in t:
            return True
    if t in _CANCEL_SHORT or re.fullmatch(r"(stop|dur)[.! ]*", t):
        return True
    return False


def classify_result(result: str) -> dict:
    """Sort a tool result into the VERIFY leg of the loop.

    Returns {"verified": bool, "failed": bool, "needs_confirm": bool}.
    Conventions come straight from the Phase D/E tools: they return
    "verified (...)" on a confirmed effect, "unverified ..." when the
    effect could not be confirmed, and plain failure text otherwise.
    """
    r = (result or "").lower()
    if _CONFIRM_TOKEN.lower() in r:
        return {"verified": False, "failed": False, "needs_confirm": True}
    if "verified" in r and "unverified" not in r:
        return {"verified": True, "failed": False, "needs_confirm": False}
    if "unverified" in r:
        return {"verified": False, "failed": False, "needs_confirm": False}
    for marker in _FAIL_MARKERS:
        if marker in r:
            return {"verified": False, "failed": True, "needs_confirm": False}
    # No marker either way: treat as a soft success (e.g. "Done.", "Opened
    # Chrome"). The model still gets a CONTINUE hint, not a RECOVER one.
    return {"verified": False, "failed": False, "needs_confirm": False}


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _summarise_args(args: dict | None) -> str:
    if not args:
        return "-"
    try:
        bits = []
        for key in ("app", "app_name", "action", "url", "query", "text",
                    "description", "target", "file_path", "angle"):
            if key in args and args[key]:
                bits.append(f"{key}={_short(args[key], 40)}")
        if not bits:
            first = next(iter(args.items()), None)
            if first:
                bits.append(f"{first[0]}={_short(first[1], 40)}")
        return ", ".join(bits[:3]) or "-"
    except Exception:
        return "-"


class TaskTracker:
    """One current task. A new user request supersedes the previous one."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._task_id = 0
        self.status = IDLE
        self.original_request = ""
        self.step_count = 0
        self.last_tool = ""
        self.last_args = ""
        self.last_result = ""
        self.last_verified = False
        self.consecutive_failures = 0
        self.failure = ""
        self.cancel_reason = ""
        self.history: list[dict] = []
        self.updated_at = 0.0
        # Conversation continuity: where the chain currently is and what the
        # latest follow-up asked, so "it/there/the first result" resolves.
        self.site_context = ""
        self.current_focus = ""
        self.follow_ups: list[str] = []

    # ── user side ────────────────────────────────────────────────────

    def note_user_request(self, text: str) -> dict:
        """Record a fresh user turn. Returns {"event": ...} for the caller.

        Events: "started" | "continued" | "cancelled" | "resumed" |
        "ignored_empty". A follow-up ("now search X", "open the first
        result") CONTINUES the active chain, keeping its goal, steps and
        site context; a genuinely new request supersedes it as before.
        """
        text = (text or "").strip()
        if not text:
            return {"event": "ignored_empty", "status": self.status}
        with self._lock:
            # Cancel wins over everything, including a pending confirmation:
            # "cancel that" in reply to the on-screen question must kill the
            # chain, not resume it.
            if self.status in (ACTIVE, AWAITING_CONFIRMATION) and is_cancel_request(text):
                self.status = CANCELLED
                self.cancel_reason = text
                self.updated_at = time.monotonic()
                return {"event": "cancelled", "status": self.status}
            if self.status == AWAITING_CONFIRMATION:
                # A reply to the confirmation question resumes the chain;
                # the gate itself (confirm.resolve) already ran or expired.
                self.status = ACTIVE
                self.updated_at = time.monotonic()
                return {"event": "resumed", "status": self.status}
            if self.status == ACTIVE and is_continuation(text):
                self.follow_ups.append(text)
                if len(self.follow_ups) > 10:
                    del self.follow_ups[:-10]
                self.current_focus = text
                self.updated_at = time.monotonic()
                return {"event": "continued", "status": self.status,
                        "task_id": self._task_id,
                        "site": self.site_context}
            if self.status == ACTIVE:
                # Supersede: keep a one-line trace, then start over.
                self.history.append({
                    "tool": "(superseded)",
                    "args": _short(self.original_request, 80),
                    "result": f"superseded by: {_short(text, 80)}",
                    "verified": False,
                    "at": time.monotonic(),
                })
            self._task_id += 1
            self.status = ACTIVE
            self.original_request = text
            self.step_count = 0
            self.last_tool = ""
            self.last_args = ""
            self.last_result = ""
            self.last_verified = False
            self.consecutive_failures = 0
            self.failure = ""
            self.cancel_reason = ""
            self.site_context = ""
            self.current_focus = ""
            self.follow_ups = []
            self.updated_at = time.monotonic()
            return {"event": "started", "status": self.status,
                    "task_id": self._task_id}

    def cancel(self, reason: str = "") -> None:
        with self._lock:
            if self.status in (ACTIVE, AWAITING_CONFIRMATION):
                self.status = CANCELLED
                self.cancel_reason = reason or "cancel requested"
                self.updated_at = time.monotonic()

    def is_active(self) -> bool:
        with self._lock:
            return self.status == ACTIVE

    # ── tool side ────────────────────────────────────────────────────

    def check_before_tool(self) -> str | None:
        """Block chaining tools after a cancel/fail. Returns a refusal or None."""
        with self._lock:
            if self.status == CANCELLED:
                return (
                    f"{_CANCELLED_TOKEN} This task was cancelled "
                    f"({ _short(self.cancel_reason, 80) or 'by the user'}). "
                    "Do NOT call any more tools for it. Say one short sentence "
                    "confirming you stopped, in the user's own language."
                )
            if self.status == FAILED:
                return (
                    "[TASK_FAILED] This task already failed "
                    f"({ _short(self.failure, 100) or 'see last result'}). "
                    "Do NOT call more tools for it. Summarise what worked and "
                    "what did not in one or two short sentences."
                )
            return None

    def record_step(self, tool: str, args: dict | None, result: str) -> dict:
        """Append a step and return its VERIFY classification + hint suffix."""
        cls = classify_result(result)
        with self._lock:
            self.step_count += 1
            self.last_tool = tool or ""
            self.last_args = _summarise_args(args)
            self.last_result = _short(result, 200)
            self.last_verified = bool(cls["verified"])
            self.updated_at = time.monotonic()
            site = _extract_site(tool, args)
            if site:
                self.site_context = site

            if cls["needs_confirm"]:
                self.status = AWAITING_CONFIRMATION
                self.history.append({
                    "tool": tool, "args": self.last_args,
                    "result": "awaiting on-screen confirmation",
                    "verified": False, "at": self.updated_at,
                })
                return {**cls, "hint": self._hint("confirm")}

            if cls["failed"] or not cls["verified"]:
                # Soft miss (unverified / unmarked) and hard failure share
                # the counter: two misses in a row end the task instead of
                # looping on a broken step forever.
                self.consecutive_failures += 1
            else:
                self.consecutive_failures = 0

            self.history.append({
                "tool": tool, "args": self.last_args,
                "result": self.last_result,
                "verified": bool(cls["verified"]), "at": self.updated_at,
            })
            if len(self.history) > 20:
                del self.history[:-20]

            if cls["failed"] and self.consecutive_failures >= 2:
                self.status = FAILED
                self.failure = _short(result, 200)
                return {**cls, "terminal": True, "hint": self._hint("fail")}
            if not cls["verified"] and self.consecutive_failures >= 3:
                self.status = FAILED
                self.failure = _short(result, 200)
                return {**cls, "terminal": True, "hint": self._hint("fail")}
            kind = ("continue" if cls["verified"]
                    else ("recover" if (cls["failed"] or "unverified" in (result or "").lower())
                          else "continue_unverified"))
            return {**cls, "terminal": False, "hint": self._hint(kind)}

    def complete(self, summary: str = "") -> None:
        with self._lock:
            if self.status == ACTIVE:
                self.status = DONE
                self.updated_at = time.monotonic()
                if summary:
                    self.last_result = _short(summary, 200)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "original_request": self.original_request,
                "step": self.step_count,
                "last_tool": self.last_tool,
                "last_args": self.last_args,
                "last_result": self.last_result,
                "failure": self.failure,
                "cancel_reason": self.cancel_reason,
                "site": self.site_context,
                "focus": self.current_focus,
            }

    # ── hint text (English: tool results are English by convention) ──

    def _hint(self, kind: str) -> str:
        n = self.step_count
        site_clause = (f" Current site: {self.site_context} — 'it', 'there' "
                       f"and 'the first result' refer to here."
                       if self.site_context else "")
        if kind == "continue":
            return (
                f"[TASK step {n} VERIFIED — CONTINUE the chain with the next "
                "step. If there is no next step, summarise briefly instead of "
                f"calling more tools.]{site_clause}"
            )
        if kind == "continue_unverified":
            return (
                f"[TASK step {n} done but UNMARKED — no verified/unverified "
                "marker. If the chain has a next step, CONTINUE; otherwise "
                "summarise briefly. Do not re-call this step.]"
                f"{site_clause}"
            )
        if kind == "recover":
            return (
                f"[TASK step {n} NOT VERIFIED — RECOVER once: retry this step "
                "exactly once with a different approach (e.g. screen_find "
                "then screen_click, or corrected parameters). If the retry "
                "also fails, STOP and report what worked and what did not. "
                "Never loop.]"
            )
        if kind == "fail":
            return (
                f"[TASK step {n} FAILED twice — FAIL the task. Do NOT call "
                "more tools for it. Summarise what worked and what did not "
                "in one or two short sentences.]"
            )
        if kind == "confirm":
            return (
                "[TASK parked for CONFIRMATION — ask the user in ONE short "
                "sentence to confirm on screen. Do NOT call more tools until "
                "they answer.]"
            )
        return ""
