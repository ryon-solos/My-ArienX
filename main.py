import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **                       kw)

    _subprocess.Popen = _Popen


# ── Console must survive non-UTF-8 code pages ────────────────────────────────
# Every status line in this file carries an emoji, and on a legacy Windows
# console the active code page is the system one — cp1254 in Turkey, cp1251 in
# Russia, cp932 in Japan. Printing an emoji there raises UnicodeEncodeError, and
# because most of these prints sit inside the receive loop it takes the session
# down on startup. Reconfiguring to UTF-8 with a replacement fallback costs
# nothing and makes the app launch the same way in every locale.
import sys as _sys

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(_sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass          # pythonw / redirected pipes / anything exotic — never fatal

# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import re
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from core.runtime_diagnostics import install as install_runtime_diagnostics, event as runtime_event
install_runtime_diagnostics()

import sounddevice as sd
import numpy as np
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
    search_memory, set_trim_notifier,
)
from memory.chat_store import ChatStore

# The file-backed tools (open_app, web_search, browser_control, …) are no longer
# imported or declared here — they self-describe via a TOOL dict in their own
# actions/*.py file and are auto-discovered by core.action_loader at startup.
# Only tools that are tied to live-session state stay inline in this file
# (screen_process, close_camera, save_memory, manage_monitor, shutdown_jarvis,
# system_status).
from actions.screen_processor  import _capture_screen
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import (
    get_brief_enabled, get_media_resolution, get_proactive_audio_enabled, get_proactive_chat_enabled,
    get_autonomous_research, get_response_detail, get_vision_mode,
    get_push_to_talk_enabled, get_thinking_enabled, get_turn_tuning, get_voice,
    get_wake_word_enabled, save_wake_word_enabled,
    get_input_device, get_output_device,
)
from core.plugin_loader        import discover_plugins
from core                      import undo as undo_stack
from core                      import confirm as confirm_gate
from core                      import audio_devices
from core.action_loader        import discover_actions
from core.task_state           import TaskTracker, CHAIN_TOOLS, classify_result
from core.agent_core           import AgentCore
from core.personality         import PersonalityManager
from core.interruption        import Interruption
from core.viseme               import VisemeStream
from core.wake_word            import (
    WakeWordDetector, is_ready as wake_is_ready, install_and_download as wake_install,
)
from core.live_speaker_diagnostics import enabled as speaker_metadata_diagnostics_enabled, summary as speaker_metadata_summary
from core.identity               import IDENTITY_POLICY
from core.cloud_bridge           import CloudBridge

# How long the assistant stays awake with no user speech before it auto-sleeps
# again (wake-word mode only).
WAKE_SLEEP_TIMEOUT = 120.0   # seconds (2 minutes)

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-3.1-flash-live-preview"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000 
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# A Live acknowledgement is expected shortly after a spoken turn finishes.
# These deliberately describe one utterance rather than a permanent socket
# state: input transcription is optional on some Live model revisions, so its
# absence must never itself trigger a reconnect.
VOICE_TURN_GAP_SECONDS = 1.0
INPUT_STALL_GRACE_SECONDS = 8.0


def live_input_turn_stalled(
        now: float, turn_started_at: float, last_voice_at: float,
        response_baseline: float, last_live_response_at: float) -> bool:
    """Whether one completed spoken turn received no server response.

    A visualizer only proves capture. This check instead ties recovery to a
    particular utterance and accepts any real Live response (audio, server
    content, or a tool call), not only optional input-transcription text.
    """
    return bool(
        turn_started_at
        and last_voice_at >= turn_started_at
        and now - last_voice_at >= INPUT_STALL_GRACE_SECONDS
        and last_live_response_at <= response_baseline
    )

# RMS below which 16-bit PCM is treated as room silence; above _LEVEL_FULL it
# reads as a full-height waveform. Tuned so ordinary speech lands mid-range and
# the bars still move for a quiet talker — language- and device-independent.
_LEVEL_FLOOR = 60.0
_LEVEL_FULL  = 2600.0


def _pcm_level(samples) -> float:
    """Map a block of int16 PCM samples to a 0.0–1.0 loudness level for the HUD
    waveform. Returns 0.0 on empty/invalid input so it can never raise."""
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))


# ── Viseme extraction ─────────────────────────────────────────────────────────
# The avatar's mouth used to be driven by one RMS value per ~200 ms write batch,
# which is five updates a second averaged over a fifth of a second — it could
# only ever flap. These read the *shape* of each 20 ms slice straight from the
# spectrum of the audio being played, so no transcript, no forced alignment and
# no language assumption: it works the same for Turkish and English.
#
# Two numbers come out. Openness tracks the first formant — F1 climbs as the jaw
# drops, so /a/ reads open and /i/ or /u/ read closed. Width tracks the second —
# F2 is high for spread vowels (/i/, /e/) and low for rounded ones (/u/, /o/).
# Extra time beyond the device's reported output latency before the microphone
# is trusted again: covers room decay and the speaker's own settling.
_TAIL_MARGIN = 0.25

_VIS_WIN = 1024        # ~43 ms analysis window at 24 kHz: enough for formants
_VIS_HOP = 480         # 20 ms between frames, i.e. 50 shapes a second

# Delay from handing the first bytes of a reply to an already-running output
# stream to hearing them: one callback period, plus whatever the DAC adds.
_FIRST_SOUND = CHUNK_SIZE / RECEIVE_SAMPLE_RATE      # ~43 ms
# How far past the device's own buffer the mouth's timeline may drift before it
# is re-anchored. The buffer is the hard limit on how much audio can be queued
# ahead, so anything beyond it plus a margin for clock error is impossible.
_CURSOR_SLACK = 0.15

# Erring early is the safe direction. A viewer tolerates a mouth that moves
# slightly before the sound far better than one that moves after it — the
# broadcast limits are about 45 ms of lag against 125 ms of lead — so where
# this is uncertain it is biased to lead.


def _pcm_visemes(samples, sr: int = 24000):
    """Slice a PCM block into (level, openness, width) frames, one per 20 ms.

    Returns [] on anything unexpected — the mouth falls back to loudness-only
    articulation rather than the caller having to handle an error.
    """
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size < _VIS_WIN:
            return []
        win = np.hanning(_VIS_WIN).astype(np.float32)
        freqs = np.fft.rfftfreq(_VIS_WIN, 1.0 / sr)
        b_f1_lo = (freqs >= 150) & (freqs < 450)     # F1 of close vowels
        b_f1_hi = (freqs >= 450) & (freqs < 1100)    # F1 of open vowels
        b_f2_bk = (freqs >= 600) & (freqs < 1300)    # F2 of rounded vowels
        b_f2_fr = (freqs >= 1700) & (freqs < 3200)   # F2 of spread vowels
        b_hiss = (freqs >= 3800) & (freqs < 8000)    # fricatives

        # One frame per hop across the *whole* block. Stepping only while a full
        # window fits stopped 1024 - 480 samples short of the end, so a 200 ms
        # batch yielded 160 ms of schedule: the mouth ran out of frames before
        # the audio ran out of sound, and each batch no longer lined up with the
        # end of the one before it. Losing 20 % of every batch is most of why
        # the mouth did not track the words.
        out = []
        for start in range(0, x.size, _VIS_HOP):
            # The level gates closures, so it is measured over exactly this
            # 20 ms and never looks ahead. The spectrum needs a longer window
            # to resolve formants and may be short-filled at the very end.
            level = _pcm_level(x[start:start + _VIS_HOP])
            seg = x[start:start + _VIS_WIN]
            if seg.size < _VIS_WIN:
                seg = np.concatenate([seg, np.zeros(_VIS_WIN - seg.size,
                                                    dtype=np.float32)])
            if level <= 0.0:
                out.append((0.0, 0.0, 0.0))
                continue
            mag = np.abs(np.fft.rfft((seg - seg.mean()) * win))
            f1l, f1h = float(mag[b_f1_lo].sum()), float(mag[b_f1_hi].sum())
            f2b, f2f = float(mag[b_f2_bk].sum()), float(mag[b_f2_fr].sum())
            hiss = float(mag[b_hiss].sum())

            openness = f1h / (f1l + f1h + 1e-6)
            width = (f2f - f2b) / (f2f + f2b + 1e-6)
            # A wide-open jaw physically cannot purse, so openness damps width.
            # /a/ has a low enough F2 to read as "rounded" on the bands alone;
            # letting openness suppress the width term is what keeps an open
            # vowel from pursing.
            width *= (1.0 - openness) ** 0.8
            # Fricatives are formed with a nearly closed mouth.
            h = hiss / (f1l + f1h + f2b + f2f + hiss + 1e-6)
            openness *= 1.0 - 0.65 * min(1.0, h * 2.5)
            out.append((level,
                        float(min(1.0, max(0.0, openness))),
                        float(min(1.0, max(-1.0, width)))))
        return out
    except Exception:
        return []


def _describe_tools(declarations) -> str:
    """One line per capability, straight from the live tool declarations.

    Derived rather than written down: the action and plugin registries are
    discovered at startup, so whatever the user has installed is what the model
    is told it can do. Adding a plugin extends this by itself, and removing one
    stops the model from claiming an ability it no longer has.
    """
    lines = []
    for d in declarations or ():
        try:
            name = d.get("name") if isinstance(d, dict) else getattr(d, "name", None)
            desc = (d.get("description") if isinstance(d, dict)
                    else getattr(d, "description", "")) or ""
        except Exception:
            continue
        if not name:
            continue
        desc = " ".join(str(desc).split())
        lines.append(f"- {name}: {desc[:150]}" if desc else f"- {name}")
    return "\n".join(lines)


def _describe_limits(has_vision: bool, has_mic: bool) -> str:
    """The other half of self-knowledge: what is out of reach, and why.

    Derived from how the program is actually built, not from a list of refusals.
    A model that knows its boundaries stops improvising around them, and stating
    them as architecture rather than as rules keeps the answer honest in any
    language.
    """
    out = [
        "- Anything not listed above is outside your reach. Say so in one clause "
        "and offer the nearest thing you can actually do — never mime an action "
        "you cannot take, and never report a result you did not get.",
        "- You act on this machine only. You cannot reach the user's other "
        "devices, accounts or hardware except through the tools listed above.",
        "- You remember what is in the memory block and what has been said this "
        "session. Anything else you were told before is gone unless it was saved.",
    ]
    if has_vision:
        out.append(
            "- Your sight is not continuous. You see nothing until you call a "
            "vision tool, and then only that single frame at that moment — you "
            "cannot watch, monitor or notice something changing on screen.")
    else:
        out.append("- You have no sight at all in this build.")
    if has_mic:
        out.append(
            "- You hear nothing while the microphone is muted, and you cannot "
            "unmute it yourself.")
    return "\n".join(out)


def _render_prompt(template: str, values: dict) -> str:
    """Fill {tokens} in the prompt template.

    A plain replace rather than str.format: the file is meant to be edited by
    hand, and a stray brace in someone's own wording must never take the app
    down at startup.
    """
    out = template or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are {assistant_name}, a personal AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

# Transcript chunks shorter than this may legitimately repeat ("evet, evet"),
# so only longer ones are treated as duplicates.
_REPEAT_MIN = 12


def _is_repeat_chunk(txt: str, buf: list) -> bool:
    """True if this transcript chunk has already been seen this turn.

    Guards against the API re-sending the tail of a response across the several
    turn_completes a tool-using turn produces.
    """
    if len(txt) < _REPEAT_MIN:
        return bool(buf) and txt == buf[-1]
    joined = " ".join(buf)
    return txt in joined

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    # ── Inline tools ─────────────────────────────────────────────────────────
    # These stay here (rather than in an actions/*.py TOOL dict) because their
    # handling is woven into live-session state — vision capture/injection,
    # camera stream, memory writes, the monitor engine, and shutdown. All other
    # tools live in their own action file and are auto-discovered by
    # core.action_loader (see JarvisLive.__init__).
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when the user says (in ANY language): close camera, stop camera, "
            "turn off camera, that's creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "JARVIS checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call only when the user explicitly asks to close or exit the app. "
            "A goodbye or a request to stop speaking does not authorize exiting. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Look up a fact you have stored about the user but which is NOT in "
            "the memory block of your system prompt. "
            "The prompt lists the keys it did not have room for under "
            "'[ALSO REMEMBERED]' — if the user asks about anything named there, "
            "call this FIRST. "
            "Also call it before saying you do not know something personal, and "
            "when the user asks what you remember about them (leave query empty "
            "for everything). "
            "This is a local file search: it is instant and costs nothing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Keyword to search for — a name, a topic, a category "
                        "(e.g. 'ayse', 'coffee', 'projects'). "
                        "Leave empty to list everything stored."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "undo",
        "description": (
            "Reverse the last change YOU made to this computer — a file you "
            "moved, renamed, created or wrote, or a setting you changed such as "
            "volume, brightness, dark mode or WiFi. "
            "Call this whenever the user says undo, revert, take it back, put it "
            "back, cancel that, or tells you that you did the wrong thing, in ANY "
            "language. "
            "Use action='list' when they ask what can be undone. "
            "This only covers your own actions — it is not the Ctrl+Z of whatever "
            "application is on screen (that is computer_settings with action 'undo')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (default) — reverse the last change | list — show what can be undone",
                },
            },
            "required": [],
        },
    },
]

class _ReconnectSignal(Exception):
    """Raised inside the session TaskGroup to force a clean, voluntary reconnect
    (e.g. the user picked a new voice — the voice is fixed at connect time, so
    the session must be rebuilt).

    Carries `keep_context`: True for an ordinary rebuild, where the stored
    resumption handle is replayed and the conversation continues; False when the
    new session must genuinely start clean (see the voice-change note in
    _on_voice_change)."""

    def __init__(self, keep_context: bool = True):
        super().__init__()
        self.keep_context = keep_context


def _is_reconnect_signal(exc: BaseException) -> bool:
    """True if `exc` is a _ReconnectSignal, or a(n) (Base)ExceptionGroup that
    wraps one — TaskGroup bundles child exceptions into a group."""
    if isinstance(exc, _ReconnectSignal):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_reconnect_signal(sub) for sub in exc.exceptions)
    return False


def _keep_context_of(exc: BaseException) -> bool:
    """Read `keep_context` off a reconnect signal, unwrapping the group the
    TaskGroup put it in. Defaults to True: an unexpected shape must not silently
    wipe the conversation."""
    if isinstance(exc, _ReconnectSignal):
        return getattr(exc, "keep_context", True)
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            if _is_reconnect_signal(sub):
                return _keep_context_of(sub)
    return True


async def _write_audio_safely(stream, data: bytes) -> None:
    """Do not close native audio handles until their executor write finishes."""
    write = asyncio.create_task(asyncio.to_thread(stream.write, data))
    try:
        await asyncio.shield(write)
    except asyncio.CancelledError:
        # Cancelling to_thread does not stop the native write. Closing its ALSA
        # handle here can segfault the entire process during a reconnect.
        try:
            await write
        finally:
            raise


class JarvisLive:
    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self._asst_name     = "ArienX"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._live_tool_queue = None
        self._cancelled_live_tools = set()
        self._loop                     = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True while the user-controlled camera stream is live
        self._vision_close_pending = False   # retained for compatibility with older session state
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._response_pending = False
        self._interrupted          = False   # True while draining audio after user interrupt
        self._interrupt_origin     = "none"    # diagnostics: barge | server | ui | none
        # Transcript-driven mouth shapes for the avatar. Fed from the receive
        # loop as words arrive, drained by the playback loop against the audio.
        self._visemes              = VisemeStream()
        self._last_out_logged      = ""      # de-dupes a re-sent transcript tail
        # Push-to-talk
        self._ptt_enabled          = False
        self._ptt_held             = False
        self._ptt                  = None    # core.hotkey.PushToTalk
        self._out_level            = 0.0     # level of the audio being played right now
        self._speech = Interruption()
        self._last_confirmed_input_at = -10.
        self._speech_diag_at = 0.
        self._proactive_pending_at = 0.0
        # `stream.write()` returns when the buffer accepts the audio, not when the
        # speaker has finished with it, so sound is still in the room after the
        # speaking flag drops. Streaming the microphone during that gap is how an
        # assistant ends up answering itself. Measured from the device rather than
        # guessed; see _play_audio.
        self._out_latency          = 0.20    # seconds, replaced with the real value
        self._tail_until           = 0.0     # monotonic time the echo tail expires
        # Wall-clock time at which the audio written next will begin to sound.
        # The mouth is scheduled against this, never against "now": batches are
        # handed to the device far faster than they play, so "now" ran the lips
        # ahead of the words and cut every schedule short. 0 = nothing playing.
        self._play_cursor          = 0.0
        self.ui.on_push_to_talk   = self.set_push_to_talk
        self.ui.ptt_hold          = self._on_ptt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_voice_change   = self._on_voice_change     # voice picker → rebuild session
        self.ui.on_audio_device_change = self._on_audio_device_change
        self.ui.get_chats = self._get_chats
        self.ui.on_chat_create = self._new_chat
        self.ui.on_chat_select = self._select_chat
        self.ui.on_chat_rename = self._rename_chat
        self.ui.on_chat_delete = self._delete_chat
        self.ui.on_chat_cloud_delete = self._delete_cloud_chat
        self._reconnect_event: asyncio.Event | None = None
        self._reconnect_keep = True   # False → next rebuild drops the resumption handle

        # ── Session resumption ─────────────────────────────────────────
        # The server issues a resumption handle every few seconds and reissues
        # it as the conversation moves on. Before this, session_resumption was
        # switched ON in the config and the update was never read, so the handle
        # was thrown away and EVERY reconnect — a dropped packet, a voice change,
        # switching microphone — started an empty session. "Unlimited sessions"
        # leaked through exactly this hole.
        #
        # Deliberately in RAM only, never written to disk. Persisting it would
        # make a fresh launch continue yesterday's conversation, which sounds
        # appealing but breaks the session-summary flow: _save_session_summary
        # runs at shutdown and the morning briefing pops it the next day. A
        # conversation that never ends never produces a summary, and the
        # "yesterday we talked about…" line silently disappears.
        self._resume_handle: str | None = None
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._cloud_bridge  = CloudBridge()
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._active_tools = 0
        self._proactive_turn = False
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        # Mic capture can stay healthy while a preview Live socket stops
        # acknowledging a turn. These timestamps distinguish that state from a
        # dead microphone and let the existing reconnect path recover it.
        self._last_voice_forwarded_at = 0.0
        self._last_stall_reconnect_at = 0.0
        self._last_live_response_at = 0.0
        self._voice_turn_started_at = 0.0
        self._voice_turn_response_baseline = 0.0
        self._realtime_drop_count = 0
        self._session_log: list[str] = []          # conversation turns for end-of-session summary
        self._chat_store = ChatStore()
        self._conversation_sync_lock = threading.Lock()
        self._active_chat = self._chat_store.ensure_active()["id"]
        self.ui.refresh_chats(self._active_chat, self._chat_store.messages(self._active_chat))

        # ── Phase F: minimal multi-action task state ───────────────────
        # Passive tracker only: the model still chains tools itself, one per
        # turn. This remembers original request / step / last result /
        # failure-cancel state so each chaining tool result can carry an
        # ACTION → VERIFY → CONTINUE / RECOVER / FAIL hint. Voice/audio
        # paths are untouched; confirmation gates stay authoritative.
        self._task = TaskTracker()
        self._agent_core = AgentCore(
            logger=self.ui.write_log,
            research_mode=get_autonomous_research(),
            vision_mode=get_vision_mode(),
        )

        # ── Phase 13: adaptive personality (internal routing, no tool) ──
        # Detection is deterministic (see core/personality); research uses
        # the existing web_search handler + core.gemini on a daemon thread.
        # Base MARK identity is never overwritten — the profile renders as
        # an additive prompt block only.
        from actions.web_search import web_search as _ws_fn
        from core import gemini as _gemini_mod
        self._personality = PersonalityManager(
            Path(__file__).resolve().parent,
            search_fn=lambda p: _ws_fn(p),
            gemini_as_json=_gemini_mod.as_json,
            send=self.plugin_say,
            log=self.ui.write_log,
        )

        self._enhanced_live = True  # proactive audio; auto-disabled if the server rejects it
        self._tuned_live    = True  # turn-taking / media / thinking knobs; same fallback

        _base_dir = Path(__file__).resolve().parent
        _inline_names = {t["name"] for t in TOOL_DECLARATIONS}

        # File-backed tools: every actions/*.py with a TOOL dict, discovered the
        # same way plugins are. Reserved names = the inline tools above, so an
        # action can never shadow one.
        self._action_registry = discover_actions(
            actions_dir=_base_dir / "actions",
            reserved_names=_inline_names,
            logger=lambda msg: print(f"[Actions] {msg}"),
        )

        # Plugins must not collide with either an inline tool or a discovered action.
        _core_names = _inline_names | self._action_registry.names()
        self._plugin_registry = discover_plugins(
            plugins_dir=_base_dir / "plugins",
            core_tool_names=_core_names,
            # Console gets the full boot transcript; the activity log gets only
            # what the user has to know about. Every plugin loading correctly is
            # the expected case and does not belong in their conversation.
            logger=lambda msg: print(f"[Plugins] {msg}"),
            notify=lambda msg: self.ui.write_log(f"SYS: {msg}"),
        )
        self.ui.get_plugins = self._plugin_registry.list_for_ui
        self.ui.get_plugin_settings = self._plugin_registry.settings_schemas  # ⚙ settings tab
        self.ui.request_say = self.plugin_say   # plugins: mid-task speech channel

        # ── Wake word ────────────────────────────────────────────────────────
        # _awake gates the mic (see _listen_audio) and the background speakers.
        # It is True whenever wake word is OFF, so default behaviour is unchanged.
        self._wake_enabled     = get_wake_word_enabled()
        self._awake            = not self._wake_enabled
        self._wake_detector: WakeWordDetector | None = None
        self._wake_sleep_timeout = WAKE_SLEEP_TIMEOUT

        # Restore the saved push-to-talk preference. Doing it here rather than
        # in __init__ means the hotkey thread only exists once there is a
        # session to talk to.
        if get_push_to_talk_enabled():
            try:
                self.set_push_to_talk(True)
            except Exception as e:
                print(f"[JARVIS] ⚠ Push-to-talk unavailable: {e}")
        # UI control surface for the Wake Word settings section.
        self.ui.wake_is_ready    = wake_is_ready          # () -> bool
        self.ui.wake_get_state   = self._wake_state       # () -> dict
        self.ui.on_wake_toggle   = self._ui_wake_toggle   # (enable: bool) -> str
        self.ui.on_wake_manual   = self._ui_wake_manual   # () -> toggle awake/asleep
        self.ui.on_wake_install  = self._ui_wake_install  # () -> (ok, msg)

    # ── Wake word: state machine ─────────────────────────────────────────────

    def _wake_state(self) -> dict:
        # A loaded, running detector is definitively ready; otherwise fall back
        # to the cheap on-disk model-file check (no Model construction).
        ready = bool(self._wake_detector and self._wake_detector.ready) or wake_is_ready()
        return {"enabled": self._wake_enabled, "awake": self._awake, "ready": ready}

    def _ensure_wake_detector(self) -> bool:
        """Load the detector once (model loads on first start). Idempotent."""
        if self._wake_detector is None:
            self._wake_detector = WakeWordDetector(
                on_detect=self._on_wake_detected,
                logger=lambda m: print(f"[Wake] {m}"),
                notify=lambda m: self.ui.write_log(f"SYS: {m}"),
            )
        if not self._wake_detector.ready:
            return self._wake_detector.start()
        return True

    def _on_wake_detected(self) -> None:
        """Called from the detector thread when 'Hey Jarvis' is heard."""
        self.wake(reason="wake word")

    def wake(self, reason: str = "wake word") -> None:
        if self._awake:
            return
        self._awake = True
        self._last_user_speech = time.monotonic()   # start the auto-sleep clock now
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: Awake — {reason}.")

    def sleep(self, reason: str = "timeout") -> None:
        if not self._awake:
            return
        self._awake = False
        self.set_speaking(False)
        self.ui.set_state("SLEEPING")
        self.ui.write_log(f"SYS: Sleeping — {reason}. Say 'Hey Jarvis' to wake me.")

    async def _run_sleep_watch(self) -> None:
        """Auto-sleep after the configured silence window (wake-word mode only)."""
        while True:
            await asyncio.sleep(5)
            if not self._wake_enabled or not self._awake:
                continue
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue
            if (time.monotonic() - self._last_user_speech) > self._wake_sleep_timeout:
                self.sleep(reason="no speech for 2 minutes")

    # ── Wake word: UI callbacks (called from the Qt thread) ──────────────────

    def _ui_wake_toggle(self, enable: bool) -> str:
        """Enable/disable wake word from the settings UI. Returns a status token:
        'enabled' | 'disabled' | 'need_download'."""
        if enable:
            if not wake_is_ready():
                return "need_download"
            self._wake_enabled = True
            save_wake_word_enabled(True)
            self._ensure_wake_detector()
            self.sleep(reason="wake word enabled")
            return "enabled"
        else:
            self._wake_enabled = False
            save_wake_word_enabled(False)
            self.wake(reason="wake word disabled")
            return "disabled"

    def _ui_wake_manual(self) -> None:
        """Manual sleep/wake button in the UI."""
        if not self._wake_enabled:
            return
        if self._awake:
            self.sleep(reason="you tapped sleep")
        else:
            self.wake(reason="you tapped wake")

    def _ui_wake_install(self) -> tuple[bool, str]:
        """Download openwakeword + the model (runs in a UI worker thread)."""
        # Triggered by the user pressing the button, so its progress is exactly
        # what they are waiting to see.
        return wake_install(logger=lambda m: print(f"[Wake] {m}"),
                            notify=lambda m: self.ui.write_log(f"SYS: {m}"))

    def plugin_say(self, instruction: str) -> None:
        """
        Thread-safe speech channel for plugins: lets a plugin ask JARVIS to
        say something short WHILE its run() is still executing (plugins block
        their executor thread, so they can't speak through the tool response
        until they finish). The instruction is injected into the Live session
        exactly like a proactive check-in; Gemini phrases it naturally in the
        user's language. Silently a no-op when no session is connected.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not self.session:
            return

        async def _say():
            try:
                self._response_pending = True
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": instruction}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[PluginSay] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), loop)
        except Exception as e:
            print(f"[PluginSay] {e}")

    def request_reconnect(self, keep_context: bool = True, reason: str = ""):
        """Thread-safe: ask the run loop to tear down and rebuild the Live
        session. Called from the Qt thread. No-op until the async loop and
        reconnect event exist.

        `keep_context=False` drops the resumption handle so the new session
        starts empty — only for changes the server cannot apply to a resumed
        session."""
        loop = getattr(self, "_loop", None)
        ev   = self._reconnect_event
        self._reconnect_keep   = keep_context
        self._reconnect_reason = reason
        if loop and not loop.is_closed() and ev is not None:
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:
                if not loop.is_closed():
                    raise

    def _on_voice_change(self):
        """Voice picker applied.

        The voice is baked into the session at connect time, so a rebuild is
        required. It is rebuilt WITHOUT the resumption handle on purpose:
        resuming restores the server's own session state, and the safe reading
        is that it restores the voice with it — which would make the picker
        appear to do nothing. Losing context here is acceptable because changing
        voice is a deliberate, rare act; losing it on a dropped packet was not."""
        self.request_reconnect(keep_context=False, reason="new voice")

    def _on_audio_device_change(self):
        """Microphone or speaker changed. Both streams are opened inside the
        session TaskGroup, so they can only be re-opened by rebuilding it —
        but the conversation is kept, which is the whole reason resumption
        landed before this feature did."""
        self.request_reconnect(keep_context=True, reason="audio device")

    async def _watch_reconnect(self):
        """Session-scoped task: when a voluntary reconnect is requested, raise a
        signal that unwinds the TaskGroup so the run loop rebuilds the session."""
        assert self._reconnect_event is not None
        await self._reconnect_event.wait()
        self._reconnect_event.clear()
        keep   = self._reconnect_keep
        reason = getattr(self, "_reconnect_reason", "") or "settings"
        self.ui.write_log(
            f"SYS: Applying {reason} — reconnecting"
            + ("..." if keep else " (starting a fresh conversation)...")
        )
        raise _ReconnectSignal(keep_context=keep)

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    # ── Local chat history ──────────────────────────────────────────────────

    def _get_chats(self) -> list[dict]:
        return self._chat_store.list()

    def _new_chat(self):
        chat = self._chat_store.create()
        self._active_chat = chat["id"]
        self._session_log = []
        self.request_reconnect(keep_context=False, reason="new chat")
        return chat, []

    def _select_chat(self, chat_id: str):
        chat = self._chat_store.select(chat_id)
        if not chat:
            return None, []
        self._active_chat = chat["id"]
        self._session_log = []
        # A Live resumption token belongs to the previous chat. Starting clean
        # avoids leaking its hidden server context into the selected history.
        self.request_reconnect(keep_context=False, reason="switching chat")
        return chat, self._chat_store.messages(chat["id"])

    def _rename_chat(self, chat_id: str, title: str):
        return self._chat_store.rename(chat_id, title)

    def _delete_chat(self, chat_id: str):
        if not self._chat_store.delete(chat_id):
            return None, []
        if chat_id != self._active_chat:
            return self._chat_store.ensure_active(), self._chat_store.messages(self._active_chat)
        active = self._chat_store.ensure_active()
        self._active_chat = active["id"]
        self._session_log = []
        self.request_reconnect(keep_context=False, reason="deleting chat")
        return active, self._chat_store.messages(active["id"])

    def _remember_chat_turn(self, role: str, text: str) -> None:
        try:
            self._chat_store.append(self._active_chat, role, text)
            if self._cloud_bridge.configured:
                threading.Thread(target=self._sync_cloud_conversations, daemon=True).start()
        except Exception as exc:
            print(f"[Chat] save failed: {exc}")

    def _delete_cloud_chat(self, chat_id: str):
        # Hide now; the persisted pending deletion retries after an offline failure.
        self._chat_store.delete(chat_id, cloud=True)
        if chat_id == self._active_chat:
            active = self._chat_store.ensure_active()
            self._active_chat = active["id"]
            self._session_log = []
            self.request_reconnect(keep_context=False, reason="deleting cloud chat")
        active = self._chat_store.ensure_active()
        threading.Thread(target=self._sync_cloud_conversations, daemon=True).start()
        return active, self._chat_store.messages(active["id"])

    def _sync_cloud_conversations(self) -> None:
        """Serialize sync and deletion so stale uploads cannot undo a delete."""
        with self._conversation_sync_lock:
            try:
                for chat_id in self._chat_store.pending_cloud_deletes():
                    self._cloud_bridge.mobile_request("DELETE", "/api/conversations?id=" + chat_id)
                    self._chat_store.cloud_delete_done(chat_id)
                    self.ui.write_log("SYS: Chat deleted from cloud.")
                listing = self._cloud_bridge.mobile_request("GET", "/api/conversations")
                for chat_id in listing.get("deleted_ids", []):
                    self._chat_store.delete(chat_id)
                if not self._chat_store.conversation(self._active_chat):
                    active = self._chat_store.ensure_active()
                    self._active_chat = active["id"]
                    self._session_log = []
                    self.request_reconnect(keep_context=False, reason="chat deleted on another device")
                    self.ui.refresh_chats(self._active_chat, self._chat_store.messages(self._active_chat))
                for summary in self._chat_store.list():
                    local = self._chat_store.conversation(summary["id"])
                    if local:
                        remote = self._cloud_bridge.sync_conversation(local)
                        if remote:
                            self._chat_store.accept_cloud(remote)
                for remote in self._cloud_bridge.cloud_conversations():
                    self._chat_store.accept_cloud(remote)
                self.ui.refresh_chats(self._active_chat)
            except Exception as exc:
                print(f"[Cloud conversations] {exc}")
                if self._chat_store.pending_cloud_deletes():
                    self.ui.write_log("ERR: Cloud deletion is pending; it will retry on the next sync. " + str(exc))

    def _note_user_request(self, text: str) -> None:
        """Phase F: feed every user turn into the passive task tracker.

        Single choke point for typed commands, voice transcripts and
        barge-in transcripts. Cancellation ("cancel", "stop that", "iptal",
        …) marks the chain CANCELLED so the next chaining tool is refused
        with a [TASK_CANCELLED] notice; anything else starts (or supersedes)
        the task with the raw request text. Personality requests are routed
        internally first (never a task, never a tool call). Never raises;
        never blocks.
        """
        self._proactive_turn = False
        self._remember_chat_turn("user", text)
        try:
            pev = self._personality.handle(text)
            if pev.get("event") not in ("ignored", "queried"):
                print(f"[Personality] turn event: {pev.get('event')} "
                      f"{pev.get('name', '')}")
                if pev.get("event") in ("activated", "researching", "cleared"):
                    return  # personality turn: not a chaining task
        except Exception as _e:
            print(f"[Personality] handle error: {_e}")
        try:
            continuation = self._task.status in ("active", "awaiting_confirmation")
            _understanding = self._agent_core.understand(text, continuation=continuation)
            self._last_understanding = _understanding
            self._agent_core.model_route(_understanding)
            if _understanding.intent == "launch_application":
                self._agent_core.route(_understanding)
            ev = self._task.note_user_request(text)
            if ev.get("event") == "cancelled":
                self.ui.write_log("SYS: Task cancelled — stopping the chain.")
                print(f"[Task] cancelled by user: {text[:80]!r}")
            elif ev.get("event") == "started":
                print(f"[Task] started: {text[:80]!r}")
            elif ev.get("event") == "continued":
                print(f"[Task] continued (site={ev.get('site') or '-'}): {text[:80]!r}")
        except Exception:
            pass

    def _on_text_command(self, text: str):
        if not self._loop or self._loop.is_closed() or not self.session:
            self.ui.write_log("SYS: Assistant is not connected yet — retry when LISTENING appears.")
            return
        # Respect wake-word sleep: a typed command must not be answered while
        # asleep either (the sleep gate is not just for the mic). Wake first with
        # "Hey Jarvis" or the WAKE NOW button.
        if self._wake_enabled and not self._awake:
            self.ui.write_log("SYS: I'm asleep — say 'Hey Jarvis' or tap WAKE NOW first.")
            return
        self.ui.pet_event("request")
        self._note_user_request(text)
        _directive = self._agent_core.research_directive(
            getattr(self, "_last_understanding", None)
        )
        _detail = "detailed" if re.search(r"\b(in detail|detailed|thorough|comprehensive|long[- ]form|extensive)\b", text, re.I) else get_response_detail()
        _turn_text = text
        if _directive:
            _turn_text += f"\n\n{_directive}"
        _execution_directive = self._agent_core.execution_directive(
            getattr(self, "_last_understanding", None)
        )
        if _execution_directive:
            _turn_text += f"\n\n{_execution_directive}"
        _chat_context = self._chat_store.context_for(self._active_chat, text)
        _turn_text += "\n\n" + self._agent_core.fusion_directive(_chat_context)
        _orchestration = self._agent_core.orchestration_directive(
            getattr(self, "_last_understanding", None))
        if _orchestration:
            _turn_text += "\n\n" + _orchestration
        _vision_directive = self._agent_core.vision_directive(
            getattr(self, "_last_understanding", None)
        )
        if _vision_directive:
            _turn_text += f"\n\n{_vision_directive}"
        _turn_text += f"\n[AGENT RESPONSE DETAIL: {_detail}; explicit user length requests override this default.]"
        self._response_pending = True
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": _turn_text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _tail_active(self) -> bool:
        """True while the speakers may still be finishing our last sentence."""
        return time.monotonic() < self._tail_until

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self._tail_until = 0.0
        else:
            # Hold the guard open across the device's own output latency plus a
            # margin for the room. The microphone is NOT muted during it — the
            # guard still lets a genuine reply through, so answering instantly
            # still works. Only our own echo is dropped.
            self._tail_until = time.monotonic() + self._out_latency + _TAIL_MARGIN
        if not value:
            # The speech controller retains recent playback across the final
            # word and speaker-buffer drain; ending speech must not erase it.
            self._out_level = 0.0
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def set_push_to_talk(self, enabled: bool) -> str:
        """Turn hold-to-talk on or off. Returns the scope actually achieved."""
        from core.hotkey import PushToTalk

        self._ptt_enabled = bool(enabled)
        self._ptt_held = False
        if not enabled:
            if self._ptt is not None:
                self._ptt.stop()
                self._ptt = None
            return "off"

        if self._ptt is None:
            self._ptt = PushToTalk(self._on_ptt)
        scope = self._ptt.start()
        # A window-scoped chord is a real limitation, not a detail — say it once
        # in the log so nobody wonders why it does nothing while another app is
        # focused. Reporting it must never be able to undo the thing it reports.
        try:
            self.ui.write_log(
                f"SYS: Push-to-talk on — hold {self._ptt.label}"
                + ("." if scope == "global"
                   else " (works while this window is focused)."))
        except Exception:
            pass
        return scope

    def _on_ptt(self, held: bool) -> None:
        """Chord pressed or released — may arrive on the hotkey thread."""
        self._ptt_held = held
        if held:
            # Holding the key is also a way to wake it, so push-to-talk works
            # without having to say the wake word first.
            if self._wake_enabled and not self._awake:
                self._awake = True
                self._last_user_speech = time.monotonic()
        try:
            self.ui.set_state("LISTENING" if held else "SLEEPING")
        except Exception:
            pass

    def interrupt(self, origin: str = "ui") -> None:
        """Stop playback on an explicit click or confirmed local speech only."""
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if self._loop and not on_loop:
            self._loop.call_soon_threadsafe(self.interrupt, origin)
            return
        if self._interrupted:
            return
        if origin == "ui" and not (self._response_pending or self._is_speaking
                or self._active_tools or (self.audio_in_queue and not self.audio_in_queue.empty())):
            return
        self._interrupted = True
        self._proactive_turn = False
        self._interrupt_origin = origin
        if self.out_queue is not None:
            while not self.out_queue.empty():
                self.out_queue.get_nowait()
            if origin == "ui":
                self.out_queue.put_nowait({"control": "stop"})
        if self.audio_in_queue is not None:
            while not self.audio_in_queue.empty():
                self.audio_in_queue.get_nowait()
        self.set_speaking(False)
        self._visemes.reset()
        self._play_cursor = 0.
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")
        runtime_event("interrupt_requested", origin=origin, correlation=round(self._speech.match,3),
                      novel_energy=round(self._speech.novelty,3))

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        self._response_pending = True
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "ArienX").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "ArienX"
            _user_name = ""

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        # Address form is a property of the language being spoken, so it is
        # stated as a principle rather than a two-language lookup — the model
        # already knows the respectful register of whatever language it is in.
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else 'ADDRESS: Address the user with the ordinary respectful form '
                      'for a superior in the language you are currently speaking — '
                      '"sir" in English, its everyday equivalent in any other '
                      'language. Never an archaic or aristocratic form, and never '
                      'the form from a different language than the one you are '
                      'speaking in this sentence.')
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        # Everything the model is told about *itself* is derived here, not
        # written into prompt.txt: the name comes from config, the platform from
        # the host, the capability list from the registries that were just
        # discovered. Rename the assistant, add a plugin or move to another OS
        # and this follows without anyone editing a prompt.
        _all_decls = (TOOL_DECLARATIONS
                      + self._action_registry.get_tool_declarations()
                      + self._plugin_registry.get_tool_declarations())
        _names = {(d.get("name") if isinstance(d, dict) else getattr(d, "name", ""))
                  for d in _all_decls}
        sys_prompt = _render_prompt(sys_prompt, {
            "assistant_name": self._asst_name,
            "platform": f"{_platform.system()} {_platform.release()}".strip(),
            "capabilities": _describe_tools(_all_decls),
            "limits": _describe_limits(
                has_vision="screen_process" in _names,
                has_mic=True,
            ),
        })

        from memory.cloud_safe import prompt_context as cloud_memory_context
        parts = [time_ctx, identity_ctx, cloud_memory_context(),
                 "[INFORMATION POLICY]\n"
                 "Answer stable knowledge directly. For current, uncertain, or "
                 "contradicted claims, use web_search before answering. Never claim "
                 "that a plausible modern product, model, feature, or release does "
                 "not exist without verification. Respect the configured research "
                 f"mode: {get_autonomous_research()}.",
                 f"[RESPONSE DETAIL]\nUse {get_response_detail()} detail by default. "
                 "Explicit requests for detail, depth, a long answer or a word count override "
                 "all brevity/personality preferences. Cover the subject fully with sections, "
                 "examples and reasoning; do not stop after a summary. For long files use "
                 "file_controller instruction to generate the complete document rather than "
                 "squeezing it into short spoken tool arguments. For detailed research use "
                 "web_search research mode and report the evidence fully.",
                 f"[VISION POLICY]\nVision mode is {get_vision_mode()}; do not claim "
                 "to see a current screen or camera frame without a fresh observation.",
                 "[MULTI-AGENT POLICY]\nGemini Live is always the Lead Agent and "
                 "the only model that talks to the user or calls local/browser tools. "
                 "Use multi_agent_task only for genuinely complex, dependency-aware work; "
                 "workers return isolated evidence for you to verify and merge.",
                 "[APPS POLICY]\nWhen the user asks ArienX to build, add, improve, "
                 "or create an app/capability for itself, do not create a desktop project "
                 "or use a developer agent. Use extension_request so the result is installed "
                 "only in the Apps gallery. Use multi_agent_task only when it is available; "
                 "otherwise call extension_request directly with the gallery app request.",
                 "[RESPONSE ANTI-LOOP]\nAnswer each user turn once. Never repeat, paraphrase, "
                 "or narrate the same plan in multiple ways. Never mention internal workers, "
                 "provider payments, credits, prompts, or fallback strategies. For an app task, "
                 "say one short status sentence and call extension_request in the same turn. "
                 "If a tool fails, state the verified failure once and stop."]
        try:
            _pblock = self._personality.render_block()
        except Exception:
            _pblock = ""
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)
        parts.append(IDENTITY_POLICY)  # Source identity wins over conversational memory.
        if _pblock:
            parts.append("[PERMANENT SPEAKING STYLE]\n" + _pblock)

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": _all_decls}],
            # Hand back the handle captured from the last session_resumption
            # update. `handle=None` is exactly the old behaviour (ask for
            # handles, start fresh), so the first connect of a run is unchanged.
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            # Sliding-window compression: session never dies from a full context
            # window — JARVIS can stay in one conversation for hours
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=get_voice()
                    )
                )
            ),
        )
        if self._enhanced_live:
            # Proactive audio: JARVIS stays silent when speech isn't addressed
            # to it (background chatter, talking to someone else in the room).
            # (Affective dialog was dropped: gemini-3.1-flash-live does not
            #  support it, and it never reliably detected tone in practice.
            #  To restore it on a 2.5 native-audio model, add back:
            #  cfg["enable_affective_dialog"] = True )
            if get_proactive_audio_enabled():
                cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)

        if self._tuned_live:
            cfg.update(self._tuning_config())
        # Only confirmed local speech may interrupt. Server VAD must not
        # cancel a reply on a leaked speaker echo before the client agrees.
        cfg["realtime_input_config"] = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True))
        return types.LiveConnectConfig(**cfg)

    def _tuning_config(self) -> dict:
        """The optional knobs, kept apart so one bad field can be dropped wholesale.

        Every one of these is a preview-API field. If a future model release
        stops accepting any of them the connection fails at setup, so the run
        loop turns `_tuned_live` off and reconnects on the plain config rather
        than leaving the user with an assistant that will not start.
        """
        out: dict = {}

        # Screenshots and camera frames are tokenised at this resolution and then
        # stay in the session's context. 'medium' keeps on-screen text legible
        # for a fraction of a full-resolution frame.
        res = get_media_resolution()
        if res != "default":
            out["media_resolution"] = {
                "low":    types.MediaResolution.MEDIA_RESOLUTION_LOW,
                "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                "high":   types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            }[res]

        # Thinking is left at the server default deliberately. Forcing the budget
        # to zero was measured on gemini-3.1-flash-live over interleaved trials
        # and did not make the first word arrive sooner — this model does not
        # appear to deliberate on the Live path, so pinning the field only adds a
        # way for a future release to behave differently. Set "thinking_enabled"
        # in config/api_keys.json to true to let it reason instead.
        if get_thinking_enabled():
            out["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)

        return out

    async def _execute_tool(self, fc, *, proactive=False) -> types.FunctionResponse:
        if proactive:
            return types.FunctionResponse(name=fc.name, id=fc.id,
                response={"result": "[PROACTIVE_NO_TOOLS] Proactive conversation cannot run tools. Wait for an explicit user request."})
        task_id = str(fc.id or f"{fc.name}-{id(fc)}")
        self.ui.pet_event("started", task_id)
        self._active_tools = getattr(self, "_active_tools", 0) + 1
        try:
            response = await self._execute_tool_impl(fc)
        except asyncio.CancelledError:
            self.ui.pet_event("cancelled", task_id)
            raise
        except Exception:
            self.ui.pet_event("failed", task_id)
            raise
        finally:
            self._active_tools = max(0, self._active_tools - 1)
        result = str((response.response or {}).get("result", ""))
        outcome = classify_result(result)
        event = ("cancelled" if "[TASK_CANCELLED]" in result else
                 "review" if outcome["needs_confirm"] else
                 "failed" if outcome["failed"] else "completed")
        self.ui.pet_event(event, task_id)
        return response

    async def _execute_tool_impl(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        args = self._agent_core.prepare_tool(name, args)

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        # ── Phase F: refuse stale chains ─────────────────────────────────
        # After a cancel or a terminal FAIL the model must stop, not wander
        # into more tool calls for the same dead task. Inline tools (memory,
        # vision, status) always pass — only real-world chaining tools stop.
        _is_chain_tool = (
            name in CHAIN_TOOLS
            or self._action_registry.has(name)
            or self._plugin_registry.has(name)
        )
        if _is_chain_tool:
            try:
                _blocked = self._task.check_before_tool()
            except Exception:
                _blocked = None
            if _blocked:
                print(f"[Task] blocked {name} ({self._task.status})")
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": _blocked},
                )

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "recall_memory":
                # Local file search: no network, no second model. Kept out of
                # the executor deliberately — it is a dictionary scan over a few
                # hundred short strings, and a thread hop would cost more than
                # the work itself.
                result = search_memory(args.get("query", ""), limit=8)

            elif name == "undo":
                if str(args.get("action", "")).lower().strip() == "list":
                    items = undo_stack.history()
                    result = ("Things I can undo, most recent first:\n"
                              + "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
                              ) if items else "I have not changed anything I can undo yet."
                else:
                    result = await loop.run_in_executor(None, undo_stack.undo_last)

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    source = "camera" if angle == "camera" else "screen"
                    self._agent_core.vision.set_active(source, True)
                    self.ui.set_vision_status(self._agent_core.vision.snapshot())
                    try:
                        if source == "camera":
                            # The preview owns the webcam. Reusing its current
                            # frame avoids a second VideoCapture competing for
                            # the device and guarantees analysis matches view.
                            self.ui.start_camera_stream()
                            self._vision_cam_active = True
                            img_b = await loop.run_in_executor(
                                None, self.ui.camera_frame, 2.0
                            )
                            if not img_b:
                                raise RuntimeError("Camera stream did not provide a frame.")
                            mime_t = "image/jpeg"
                            print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        else:
                            img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                            print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        self._agent_core.vision.observe(source, "fresh frame captured")
                        if source == "camera":
                            self._agent_core.vision.set_active("camera", True)
                        self.ui.set_vision_status(self._agent_core.vision.snapshot())
                        _stall = source
                    except Exception:
                        self._agent_core.vision.set_active(source, False)
                        self.ui.set_vision_status(self._agent_core.vision.snapshot())
                        raise
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    # The image is attached to this same exchange, so there is
                    # nothing to stall for and nothing to announce. Asking for an
                    # acknowledgement here is what produced two spoken answers —
                    # the model filled that turn by answering the question from
                    # imagination, then answered it again once it could see.
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured and attached to this "
                        f"same exchange. Do not acknowledge and do not answer yet — the image "
                        f"is arriving with this result. Reply once, from what you actually see "
                        f"in it."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                self._vision_cam_active = False
                self._vision_close_pending = False
                self._agent_core.vision.set_active("camera", False)
                self.ui.set_vision_status(self._agent_core.vision.snapshot())
                result = "Camera closed."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "shutdown_jarvis":
                def shutdown_confirmed():
                    async def finish():
                        await self._save_session_summary()
                        self.ui.quit()
                    asyncio.run_coroutine_threadsafe(finish(), self._loop)
                    return "Closing ArienX."
                result = confirm_gate.request(
                    "shutdown_arienx", "Close ArienX?",
                    "The assistant requested to close the app. Confirm to exit.", shutdown_confirmed)

            elif self._action_registry.has(name):
                # file_processor: fall back to the currently-uploaded file when none is given
                if name == "file_processor" and not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                _ctx = {"player": self.ui, "speak": self.speak,
                        "response": None, "session_memory": None}
                r = await loop.run_in_executor(None, lambda: self._action_registry.run(name, args, _ctx))
                result = r or "Done."
                # web_search: mirror results to the on-screen content panel
                if (name == "web_search" and r
                        and not r.startswith("No results")
                        and not r.startswith("Search failed")):
                    _mode  = args.get("mode", "search")
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)

            else:
                if self._plugin_registry.has(name):
                    r = await loop.run_in_executor(
                        None,
                        lambda: self._plugin_registry.run(name, args, player=self.ui, session_memory=None)
                    )
                    result = r or "Done."
                else:
                    result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        # ── Phase F: ACTION → VERIFY → CONTINUE / RECOVER / FAIL ────────
        # The per-tool verify strings from Phase D/E are classified here and
        # a one-line TASK hint is appended so the model knows whether to
        # continue the chain, recover once, or stop. Confirmation results
        # park the task (never count as failure); the gate stays authoritative.
        if _is_chain_tool:
            try:
                _rec = self._task.record_step(name, args, result)
                _hint = _rec.get("hint") or ""
                if _hint:
                    result = f"{result}\n{_hint}"
                print(f"[Task] {self._task.status} step {self._task.step_count} "
                      f"{name} verified={_rec.get('verified')} "
                      f"failed={_rec.get('failed')}")
                if _rec.get("needs_confirm"):
                    self.ui.write_log("SYS: Task parked — awaiting on-screen confirmation.")
                elif _rec.get("terminal"):
                    self.ui.write_log(f"SYS: Task failed — {self._task.failure[:100]}")
            except Exception as _e:
                print(f"[Task] record_step error: {_e}")

        self._agent_core.complete(self._agent_core.observe(name, result))

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        # A tool that declared itself NON_BLOCKING also says when its answer may
        # re-enter the conversation. Without this the model finishes whatever it
        # was saying and then reads the result out on top of it — which, for
        # something like a phone call already ringing, is exactly the noise the
        # non-blocking call was meant to avoid. Tools that declared nothing get
        # the API default and behave as they always have.
        _sched = (self._action_registry.scheduling(name)
                  or self._plugin_registry.scheduling(name))
        _extra = {"scheduling": _sched} if _sched else {}
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result},
            **_extra
        )

    async def _send_realtime(self):
        while True:
            try:
                msg = await asyncio.wait_for(self.out_queue.get(), timeout=.1)
            except asyncio.TimeoutError:
                msg = None
            stop = bool(msg and msg.get("control") == "stop")
            if stop:
                events = self._speech.stop()
            elif msg is None:
                events = self._speech.poll()
            elif self.ui.muted and msg.get("kind") != "end":
                events = []
            elif "kind" in msg:
                events = [(msg["kind"], msg.get("data"))]
            else:  # existing phone relay, through the same speech classifier
                events = self._speech.process(np.frombuffer(msg["data"],dtype=np.int16),
                                              speaking=self._is_speaking)
            for kind, data in events:
                if kind == "interrupt":
                    self.interrupt(origin="voice")
                elif kind == "start":
                    if not stop:
                        now = time.monotonic()
                        self._last_confirmed_input_at = now
                        self._voice_turn_started_at = now
                        self._voice_turn_response_baseline = self._last_live_response_at
                        self._last_user_speech = now
                    await self.session.send_realtime_input(activity_start=types.ActivityStart())
                elif kind == "end":
                    self._response_pending = not stop
                    await self.session.send_realtime_input(activity_end=types.ActivityEnd())
                elif kind == "audio":
                    await self.session.send_realtime_input(audio=types.Blob(data=data,mime_type="audio/pcm;rate=16000"))
                    if _pcm_level(np.frombuffer(data,dtype=np.int16)) > .01:
                        self._last_voice_forwarded_at = time.monotonic()

    def _enqueue_speech_events(self, queue, events):
        if queue is not self.out_queue or not self.session or not self._awake or self.ui.muted:
            return
        for kind, data in events:
            if kind == "interrupt":
                self.interrupt(origin="voice")
                continue
            if queue.full():
                # Preserve protocol boundaries; evict only stale PCM on congestion.
                pending=[]
                while not queue.empty():
                    pending.append(queue.get_nowait())
                for index,item in enumerate(pending):
                    if item.get("kind") == "audio" or "kind" not in item:
                        pending.pop(index)
                        break
                for item in pending:
                    queue.put_nowait(item)
                if queue.full():
                    runtime_event("speech_queue_congested")
                    continue
            queue.put_nowait({"kind":kind,"data":data})

    async def _watch_input_stall(self):
        """Reconnect only after a completed spoken turn gets no Live response."""
        while True:
            await asyncio.sleep(3)
            now = time.monotonic()
            if (not self._awake or self.ui.muted or self._phone_active
                    or self._speech.active or self._active_tools or self._interrupted
                    or (self._ptt_enabled and not self._ptt_held)):
                continue
            with self._speaking_lock:
                if self._is_speaking:
                    continue
            if not live_input_turn_stalled(
                    now, self._voice_turn_started_at,
                    self._last_voice_forwarded_at,
                    self._voice_turn_response_baseline,
                    self._last_live_response_at):
                continue
            if now - self._last_stall_reconnect_at < 30:
                continue
            self._last_stall_reconnect_at = now
            self._voice_turn_started_at = 0.0
            self.ui.write_log("NET: Live input stalled after speech — reconnecting the session.")
            self.request_reconnect(keep_context=True, reason="stalled live input")

    def _enqueue_realtime_audio(self, queue, data: bytes, mime_type: str, speech=None) -> None:
        """Add mic audio only to its owning Live session, retaining newest speech.

        This runs on the asyncio loop after sounddevice's callback returns. A
        slow or dead socket can otherwise fill the bounded queue, emit uncaught
        QueueFull errors, and keep the HUD moving while no usable speech reaches
        Gemini. Dropping the oldest block preserves the current command.
        """
        if queue is None or queue is not self.out_queue:
            return
        try:
            queue.put_nowait({"data": data, "mime_type": mime_type, "speech": speech})
            return
        except asyncio.QueueFull:
            pass
        try:
            queue.get_nowait()
            queue.put_nowait({"data": data, "mime_type": mime_type, "speech": speech})
            self._realtime_drop_count += 1
            if self._realtime_drop_count == 1 or self._realtime_drop_count % 50 == 0:
                print("[JARVIS] Live mic queue saturated; retaining newest audio "
                      f"(dropped={self._realtime_drop_count}).")
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            # A reconnect may have replaced the queue between the two calls.
            return

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            if self._wake_enabled and not self._awake:
                if self._wake_detector is not None:
                    self._wake_detector.feed(indata)
                return
            if self.ui.muted or self._phone_active or (self._ptt_enabled and not self._ptt_held):
                return
            with self._speaking_lock:
                speaking = self._is_speaking
            try:
                events = self._speech.process(indata, speaking=speaking)
                queue = self.out_queue
                loop.call_soon_threadsafe(self._enqueue_speech_events, queue, events)
                self.ui.set_audio_level(min(1.,self._speech.clean_rms/2600.))
                now=time.monotonic()
                if speaking and now-self._speech_diag_at>=2:
                    self._speech_diag_at=now
                    runtime_event("speech_filter", raw_rms=round(self._speech.raw_rms),
                        residual_rms=round(self._speech.clean_rms), correlation=round(self._speech.match,3),
                        novelty=round(self._speech.novelty,3), input_active=self._speech.active)
            except Exception as exc:
                if not getattr(self,"_mic_error_logged",False):
                    self._mic_error_logged=True
                    runtime_event("microphone_callback_error",error_type=type(exc).__name__)

        try:
            def _open_mic(dev):
                return sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=dev,
                    callback=callback,
                )

            # Which microphone. resolve() returns None for "system default" and
            # for a saved device that is no longer present — so a headset
            # unplugged since the last run falls back to the built-in mic
            # instead of raising on startup and taking the session with it.
            _mic_name = get_input_device()
            _mic_dev  = audio_devices.resolve(_mic_name, "input")
            if _mic_dev is not None:
                print(f"[JARVIS] 🎤 Input device: {_mic_name}")
            try:
                _mic_stream = _open_mic(_mic_dev)
            except Exception as _e:
                # A device the picker listed but the driver will not open right
                # now — exclusive mode, a webcam already in use, a virtual mic
                # whose source went away. Chosen hardware failing must never
                # mean the assistant cannot hear at all.
                if _mic_dev is None:
                    raise
                print(f"[JARVIS] ⚠️  Mic '{_mic_name}' failed: {_e} — using default")
                self.ui.write_log(
                    f"SYS: Microphone '{_mic_name}' unavailable — using system default."
                )
                _mic_stream = _open_mic(None)

            with _mic_stream:
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _flush_pending_vision(self) -> bool:
        """Send a captured frame immediately after its tool response.

        The frame is already in hand by the time `screen_process` returns — the
        capture happened inside the tool call. The old flow still made the model
        speak a turn first and only injected the image on that turn's
        turn_complete, which cost a whole extra round trip AND produced two
        spoken answers: one improvised without the picture, then the real one.
        Sending it here means the model has the tool result and the image before
        it generates anything, so the user gets one answer, sooner.
        """
        if not (self._pending_vision and self.session):
            return False

        import base64 as _b64
        img_b, mime_t, question, angle = self._pending_vision
        self._pending_vision = None
        b64 = _b64.b64encode(img_b).decode("ascii")
        print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")

        # Label the source. Without it the image arrives carrying nothing but
        # the user's own sentence, and a screenshot of this app — which has a
        # face in the middle of it — got read as a photo of the user. What the
        # label *means* is explained once, in the generated [SELF] block.
        src = ("[IMAGE SOURCE: WEBCAM]" if angle == "camera"
               else "[IMAGE SOURCE: SCREEN CAPTURE]")
        self._response_pending = True
        await self.session.send_client_content(
            turns={"role": "user", "parts": [
                {"inline_data": {"mime_type": mime_t, "data": b64}},
                {"text": f"{src}\n\n{question}"},
            ]},
            turn_complete=True,
        )

        if self._vision_cam_active:
            # Camera remains live until the user explicitly says close/stop
            # camera. The frame is one observation; the preview is a separate
            # user-controlled stream.
            self._vision_busy = False
        else:
            self._vision_busy = False
        return True

    async def _process_live_tools(self):
        """Keep tool execution off the receive path so barge acknowledgements flow."""
        while True:
            session, calls, proactive = await self._live_tool_queue.get()
            if session is not self.session:
                continue
            responses = []
            for fc in calls:
                try:
                    if fc.id in self._cancelled_live_tools:
                        continue
                    result = await self._execute_tool(fc, proactive=proactive)
                    if fc.id not in self._cancelled_live_tools:
                        responses.append(result)
                finally:
                    self._cancelled_live_tools.discard(fc.id)
            responses = [result for result in responses
                         if result.id not in self._cancelled_live_tools]
            for fc in calls:
                self._cancelled_live_tools.discard(fc.id)
            if responses and session is self.session:
                await session.send_tool_response(function_responses=responses)
                await self._flush_pending_vision()

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():
                    # Keep this packet's origin even if turn_complete clears the flag.
                    _response_proactive = self._proactive_turn

                    # Input transcription is optional. Any substantive Live
                    # response after the current utterance proves the transport
                    # remains healthy and cancels its stall watchdog.
                    if response.data or response.server_content or response.tool_call:
                        self._last_live_response_at = time.monotonic()

                    # ── Session resumption ───────────────────────────────────
                    # The server sends this periodically. `resumable` goes false
                    # while a turn is mid-flight — replaying a handle from that
                    # moment is what the flag exists to prevent — so only
                    # resumable handles are kept. This is three lines and it is
                    # the entire fix for "every reconnect forgets everything".
                    _sru = getattr(response, "session_resumption_update", None)
                    if _sru is not None:
                        if getattr(_sru, "resumable", False) and getattr(_sru, "new_handle", None):
                            if self._resume_handle is None:
                                print("[JARVIS] 🔗 Session resumption armed")
                            self._resume_handle = _sru.new_handle

                    cancellation = getattr(response, "tool_call_cancellation", None)
                    if cancellation:
                        self._cancelled_live_tools.update(cancellation.ids or [])
                    sc = response.server_content
                    if sc is not None and getattr(sc, "interrupted", False):
                        self._response_pending = False
                        # With manual VAD, an activityStart cancels the server turn.
                        # This acknowledgement itself ends the discard window.
                        if self._interrupted:
                            self._interrupted = False
                            self._interrupt_origin = "none"
                            out_buf = []
                            self._visemes.reset()
                            if self._turn_done_event:
                                self._turn_done_event.set()
                            runtime_event("interrupt_acknowledged")
                        elif time.monotonic()-self._last_confirmed_input_at<2.:
                            self.interrupt(origin="server")
                            self._interrupted = False
                            out_buf = []
                        else:
                            # An unconfirmed server event cannot manufacture a
                            # local interruption or erase queued playback.
                            runtime_event("unconfirmed_server_interrupt")
                        transcription = getattr(sc, "input_transcription", None)
                        if transcription and transcription.text:
                            txt = _clean_transcript(transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()
                        continue

                    if response.data:
                        self._response_pending = True
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            # A turn that involves a tool call passes through
                            # several turn_completes, and the API re-sends the
                            # tail of the transcript across them. Comparing only
                            # against the previous chunk missed that — once
                            # out_buf had been flushed and emptied, the repeat
                            # sailed straight back in, which logged the answer
                            # twice AND made the avatar mouth it twice.
                            if txt and not _is_repeat_chunk(txt, out_buf):
                                out_buf.append(txt)
                                # Hand the words to the mouth as they arrive, so
                                # the avatar can form the consonants the audio
                                # alone cannot show. Pure string work — it adds
                                # nothing measurable to the response path.
                                self._visemes.feed_text(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            if speaker_metadata_diagnostics_enabled():
                                print(speaker_metadata_summary(sc.input_transcription))
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._proactive_turn = False
                                _response_proactive = False
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete:
                            self._response_pending = False
                            self._proactive_turn = False
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            # The user's barging speech IS logged: it is a real
                            # command, not an echo of the discarded reply.
                            if self._interrupted:
                                self._interrupted = False
                                barged_in = " ".join(in_buf).strip()
                                try:
                                    print(f"[Barge] USER_TURN origin={self._interrupt_origin} "
                                          f"chars={len(barged_in)}")
                                except Exception:
                                    pass
                                self._interrupt_origin = "none"
                                if barged_in:
                                    self._last_out_logged = ""   # new exchange
                                    self._note_user_request(barged_in)
                                    self.ui.write_log(f"You: {barged_in}", speaker="you")
                                    self._session_log.append(f"User: {barged_in}")
                                    if self._dashboard:
                                        asyncio.create_task(self._dashboard.broadcast({
                                            "type": "log", "speaker": "user",
                                            "text": barged_in,
                                            "ts": datetime.now().isoformat(),
                                        }))
                                in_buf  = []
                                out_buf = []
                                self._visemes.reset()
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self._last_out_logged = ""   # new exchange
                                self._note_user_request(full_in)
                                self.ui.write_log(f"You: {full_in}", speaker="you")
                                self._session_log.append(f"User: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            # Second line of defence: even if a repeat slips
                            # into a *fresh* buffer after a flush, never log the
                            # same answer (or a tail of it) twice in a row.
                            if full_out and len(full_out) >= _REPEAT_MIN and self._last_out_logged:
                                if full_out in self._last_out_logged:
                                    full_out = ""
                            if full_out:
                                self.ui.pet_event("answered")
                                self._last_out_logged = full_out
                                self.ui.write_log(f"{self._asst_name}: {full_out}", speaker="ai")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                                self._remember_chat_turn("assistant", full_out)
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                    if response.tool_call:
                        await self._live_tool_queue.put((self.session,
                            list(response.tool_call.function_calls), _response_proactive))
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        _spk_name = get_output_device()
        _spk_dev  = audio_devices.resolve(_spk_name, "output")
        if _spk_dev is not None:
            print(f"[JARVIS] 🔊 Output device: {_spk_name}")

        def _open_spk(dev):
            st = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=dev,
            )
            st.start()
            return st

        try:
            stream = _open_spk(_spk_dev)
        except Exception as _e:
            # A chosen output that the host API accepts by name but refuses to
            # open (exclusive mode, wrong sample rate, device asleep) must not
            # cost the user their voice. Fall back to the default and say so.
            if _spk_dev is None:
                raise
            print(f"[JARVIS] ⚠️  Output device '{_spk_name}' failed: {_e} — using default")
            self.ui.write_log(f"SYS: Speaker '{_spk_name}' unavailable — using system default.")
            stream = _open_spk(None)

        # Ask the device how far behind the speakers actually are, rather than
        # assuming. This is what the echo tail is sized from, so a machine with a
        # large audio buffer gets a correspondingly longer guard — and one with a
        # tiny buffer is not penalised with a delay it does not need.
        try:
            lat = float(getattr(stream, "latency", 0.0) or 0.0)
            if 0.0 < lat < 1.0:
                self._out_latency = lat
            print(f"[JARVIS] 🔊 Output latency {self._out_latency*1000:.0f} ms "
                  f"→ echo tail {(self._out_latency + _TAIL_MARGIN)*1000:.0f} ms")
        except Exception:
            pass

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue

                if self._interrupted:
                    continue
                self.set_speaking(True)

                # Batch all immediately-available chunks into one write to reduce
                # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                # Cap at ~50 ms so an interrupt leaves at most one short write in flight.
                batch = bytearray(chunk)
                while len(batch) < 2400:   # 2400 bytes ≈ 50 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # Drive the HUD waveform and the avatar's mouth from JARVIS's
                # own voice. The batch is up to 200 ms long, so we hand over a
                # *schedule* of 20 ms viseme frames instead of a single averaged
                # level and let the HUD play it out in step with the audio.
                try:
                    pcm = np.frombuffer(bytes(batch), dtype=np.int16)
                    hop = _VIS_HOP / RECEIVE_SAMPLE_RATE
                    frames = _pcm_visemes(pcm, sr=RECEIVE_SAMPLE_RATE)
                    # When does this batch become audible? The stream was
                    # started at launch and its callback has been pulling
                    # silence ever since, so the first bytes of a reply reach
                    # the speaker about one callback period later — NOT one
                    # buffer later. `stream.latency` reports the buffer's
                    # capacity, which is how much can be queued ahead, and on
                    # Windows that is commonly 300-500 ms. Anchoring on it put
                    # the entire schedule a buffer late; that is the half second
                    # of lag, and it grew with whatever the device reported.
                    #
                    # After the anchor nothing needs measuring: the device
                    # consumes at exactly realtime, so each batch sounds one
                    # batch-duration after the one before it. The cursor is
                    # re-anchored only when it leaves the range physically
                    # possible — behind `now` means the device drained and this
                    # batch starts a fresh stretch of speech, while further
                    # ahead than the buffer can hold means it has drifted.
                    now = time.time()
                    horizon = self._out_latency + _CURSOR_SLACK
                    if not (now <= self._play_cursor <= now + horizon):
                        self._play_cursor = now + _FIRST_SOUND
                    at = self._play_cursor
                    # Advance by the batch's own duration whether or not it
                    # yielded frames, so a block too short to analyse cannot
                    # shift everything after it out of step with the audio.
                    self._play_cursor += pcm.size / RECEIVE_SAMPLE_RATE
                    # Echo matching uses RMS consistently, not HUD peak levels.
                    if frames:
                        frames = self._visemes.frames(frames, hop)
                        self.ui.push_visemes(frames, hop, at)
                        # Barge-in needs to know what we are playing, not just
                        # how loud: the guard subtracts this from the microphone.
                        self._out_level = max(f[0] for f in frames)
                    else:
                        lvl = _pcm_level(pcm)
                        self.ui.set_audio_level(lvl)
                        self._out_level = lvl
                except Exception:
                    pass

                self._speech.playback(np.frombuffer(bytes(batch),dtype=np.int16), RECEIVE_SAMPLE_RATE)
                try:
                    await _write_audio_safely(stream, bytes(batch))
                except asyncio.CancelledError:
                    raise
                except RuntimeError:
                    raise
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def _run_cloud_heartbeat(self):
        from core.cloud_bridge import get_config as bridge_config
        bridge = CloudBridge()
        last_failure = ""
        while True:
            bridge.cfg = bridge_config()
            if bridge.configured:
                try:
                    okay = await asyncio.to_thread(bridge.heartbeat, ["computer", "files", "screen", "camera"])
                    if not okay and bridge.last_trace and bridge.last_trace[-1].get("status") == 401:
                        # Only rejected device credentials need registration;
                        # a transient timeout must not churn the pairing.
                        if await asyncio.to_thread(bridge.register, "ArienX desktop"):
                            okay = await asyncio.to_thread(bridge.heartbeat, ["computer", "files", "screen", "camera"])
                    if not okay and bridge.last_error != last_failure:
                        last_failure = bridge.last_error
                        self.ui.write_log("NET: Cloud heartbeat delayed; pairing retained and retrying.")
                        runtime_event("cloud_heartbeat_failed", status=bridge.last_trace[-1].get("status", 0) if bridge.last_trace else 0)
                    elif okay and last_failure:
                        last_failure = ""
                        runtime_event("cloud_heartbeat_recovered")
                except Exception as exc:
                    runtime_event("cloud_heartbeat_error", error_type=type(exc).__name__)
            await asyncio.sleep(20)

    async def _run_cloud_bridge(self):
        """App-lifetime sync, independent of voice reconnects and heartbeats."""
        from core.cloud_bridge import get_config as bridge_config
        while True:
            self._cloud_bridge.cfg = bridge_config()
            if self._cloud_bridge.configured:
                try:
                    await asyncio.to_thread(self._cloud_bridge.publish_mobile_snapshot)
                    sync_state = await asyncio.to_thread(self._cloud_bridge.sync_cloud_safe)
                    if sync_state == "conflict":
                        self.ui.write_log("NET: Cloud-safe memory changed elsewhere — sync paused to prevent overwrite.")
                    await asyncio.to_thread(self._sync_cloud_conversations)
                    tasks = await asyncio.to_thread(self._cloud_bridge.poll_tasks)
                    for task in tasks:
                        try:
                            fc = types.FunctionCall(id=str(task["id"]), name=str(task["action"]), args=dict(task.get("args") or {}))
                            response = await self._execute_tool(fc)
                            result = str(getattr(response, "response", {"result": "completed"}))
                        except Exception as exc:
                            result = f"Local execution failed: {type(exc).__name__}"
                        await asyncio.to_thread(self._cloud_bridge.complete_task, str(task.get("id", "")), result)
                except Exception as exc:
                    runtime_event("cloud_sync_error", error_type=type(exc).__name__)
            await asyncio.sleep(30)

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        # The briefing fires before the user has said anything, so the
        # remembered language is the only signal there is. It is a starting
        # point, not a setting: the moment they reply, their language wins.
        lang_clause = (f" Speak this greeting in {lang}, then follow the "
                       f"user's own language from their first reply onward."
                       if lang else "")
        name_clause = f" Address the user as {name}." if name else ""

        # Inject last session context if available — pop removes it so it's never repeated
        last = await asyncio.to_thread(pop_last_session)
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet the user warmly, mention it is {time_str}, and say you are fetching today's news now.{session_clause} "
            f"Keep it to 2 short sentences max. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        self._response_pending = True
        await self.session.send_client_content(
            turns={"role": "user", "parts": [{"text": p1}]},
            turn_complete=True,
        )
        print("[JARVIS] Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = (f" Speak in {lang} unless the user has since "
                            f"spoken another language, in which case use theirs."
                            if lang else "")

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # Extra buffer: turn_complete fires when Gemini finishes *generating*
                # Phase 1, but audio may still be playing.  Waiting a beat here
                # prevents Phase 2 audio from arriving while Phase 1 is mid-sentence
                # (which sounds like a "repeated first response" to the user).
                if turn_waited:
                    await asyncio.sleep(0.8)
                else:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=8.0)
                except Exception as e:
                    self.ui.write_log(f"SYS: News fetch timed out/failed: {e!r}")
                    news_text = ""

                if not self.session:
                    return

                failed = (not news_text) or news_text.startswith(
                    ("No news found", "Search failed", "Please provide")
                )
                if not failed:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    self.ui.write_log(
                        f"SYS: News unavailable — backend returned: {news_text[:120]!r}"
                    )
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                self._response_pending = True
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": p2}]},
                    turn_complete=True,
                )
                print("[JARVIS] Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                print(f"[JARVIS] Briefing phase 2 failed: {e}")
                self.ui.write_log("SYS: Could not fetch the news for the briefing.")

        asyncio.create_task(_deliver_news())

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from core import gemini
            summary = await asyncio.to_thread(
                gemini.text, prompt, gemini.SMART, None, 30_000,
            )
            if summary:
                save_session_summary(summary, lang)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session or not self._awake:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                self._response_pending = True
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session and self._awake:
                # Don't interrupt if user spoke recently or JARVIS is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            self._response_pending = True
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            print("[JARVIS] Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Proactive mode ──────────────────────────────────────────────────────────

    def _proactive_allowed(self) -> bool:
        if (not self.session or not self._awake or self.ui.muted
                or self._phone_active or self._ptt_enabled or self._interrupted
                or self._active_tools or self._proactive_turn
                or self._task.status == "awaiting_confirmation"):
            return False
        with self._speaking_lock:
            if self._is_speaking:
                return False
        if self._tail_active() or (self.audio_in_queue and not self.audio_in_queue.empty()):
            return False
        now = time.monotonic()
        if self._speech.active:
            return False
        # An old unanswered/noisy utterance cannot suppress conversation forever.
        if self._voice_turn_started_at > self._last_live_response_at and now-self._voice_turn_started_at < 12:
            return False
        return self._proactive.should_trigger(self._last_user_speech)

    async def _run_proactive_mode(self) -> None:
        """Occasional memory-based conversation, with no autonomous tool actions."""
        while True:
            await asyncio.sleep(10)
            if self._proactive_turn and time.monotonic()-self._proactive_pending_at > 30:
                self._proactive_turn = False
            if not get_proactive_chat_enabled() or not self._proactive_allowed():
                continue
            try:
                memory = await asyncio.to_thread(load_memory)
                monitors = await asyncio.to_thread(list_monitors)
                prompt = self._proactive.build_prompt(memory, monitors or None,
                    self._session_log[-8:] if self._session_log else None)
                # Recheck after slow memory reads: the user may have started talking.
                if not get_proactive_chat_enabled() or not self._proactive_allowed():
                    continue
                self._proactive.mark_triggered()
                self._proactive_turn = True
                self._proactive_pending_at = time.monotonic()
                self.ui.write_log("SYS: Gojo is starting a conversation.")
                self._response_pending = True
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": prompt}]},
                    turn_complete=True)
            except Exception as e:
                self._proactive_turn = False
                print(f"[Proactive] {type(e).__name__}: check-in deferred")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            # Phone input uses the same controller as local speech, including
            # while replying. Dropping it here would make interruption impossible.
            if not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    # A remote command is deliberate control and the phone user
                    # has no desktop WAKE button — so it wakes JARVIS if asleep.
                    if self._wake_enabled and not self._awake:
                        self.wake(reason="remote command")
                    self._note_user_request(text)
                    self._response_pending = True
                    await self.session.send_client_content(
                        turns={"role": "user", "parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def _run_dashboard(self):
        dashboard = self._dashboard
        try:
            await dashboard.serve()
            if getattr(dashboard, "last_error", ""):
                self.ui.write_log("SYS: " + dashboard.last_error + " Desktop voice and text remain enabled.")
        except (SystemExit, Exception) as exc:
            runtime_event("dashboard_startup_error", error_type=type(exc).__name__)
            self.ui.write_log("SYS: Remote dashboard unavailable; desktop voice and text remain enabled.")

    async def run(self):
        self._loop = asyncio.get_event_loop()
        self._reconnect_event = asyncio.Event()

        # ── Wire the shared core services to the interface ───────────────────
        # The confirmation gate is useless without a way to ask, and a memory
        # trim is invisible without a way to say so. Both are bound once here
        # rather than passed down through every action signature.
        confirm_gate.bind(
            show = self.ui.show_confirm,
            hide = self.ui.hide_confirm,
            log  = self.ui.write_log,
        )
        set_trim_notifier(self.ui.write_log)

        # Tell the device picker the exact rates the streams open at, from the
        # constants that actually open them — so it can never list a device that
        # cannot be opened at them.
        audio_devices.configure(SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE)

        # Enumerate audio devices off-thread. The settings drawer must never pay
        # for host-API enumeration on the Qt thread.
        audio_devices.prefetch()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._run_dashboard())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        # Retained across Live reconnects; these tasks are canceled at app shutdown.
        self._cloud_tasks = [asyncio.create_task(self._run_cloud_heartbeat()),
                             asyncio.create_task(self._run_cloud_bridge())]
        while True:
            try:
                print("[JARVIS] Connecting...")
                self.ui.set_state("THINKING")
                _resumed_with = self._resume_handle is not None
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                # v1alpha carries proactive audio; if it gets rejected we fall
                # back to v1beta.
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1alpha" if self._enhanced_live else "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._live_tool_queue = asyncio.Queue(maxsize=32)
                    self._cancelled_live_tools = set()
                    self._turn_done_event = asyncio.Event()
                    self._last_voice_forwarded_at = 0.0
                    self._last_live_response_at = 0.0
                    self._voice_turn_started_at = 0.0
                    self._voice_turn_response_baseline = 0.0
                    self._speech.silence_seconds=max(.5,min(1.2,get_turn_tuning()["silence_ms"]/1000))
                    self._speech.reset()
                    self._last_confirmed_input_at=-10.
                    self._mic_error_logged=False
                    self.ui.write_log("SYS: Code-only speech filter active; speaker echo is checked before input starts.")
                    self._realtime_drop_count = 0

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._agent_core.vision.set_active("screen", False)
                    self._agent_core.vision.set_active("camera", False)
                    self.ui.set_vision_status(self._agent_core.vision.snapshot())
                    self._interrupted          = False
                    self._response_pending = False
                    self._proactive_turn = False
                    self._interrupt_origin     = "none"

                    print("[JARVIS] Connected.")
                    if _resumed_with:
                        # Say it plainly: the difference between "it reconnected"
                        # and "it reconnected and still knows what we were doing"
                        # is the whole point, and it is invisible otherwise.
                        self.ui.write_log("SYS: Reconnected — conversation restored.")

                    # Wake word: if enabled, come up ASLEEP (mic gated, silent)
                    # until the user says "Hey Jarvis" or taps wake in the UI.
                    if self._wake_enabled:
                        self._ensure_wake_detector()
                        self._awake = False
                        self.ui.set_state("SLEEPING")
                        self.ui.write_log("SYS: JARVIS online — sleeping. Say 'Hey Jarvis' to wake me.")
                    else:
                        self._awake = True
                        self.ui.set_state("LISTENING")
                        self.ui.write_log("SYS: JARVIS online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    self._reconnect_event.clear()  # ignore requests from before this session
                    tg.create_task(self._watch_reconnect())
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._process_live_tools())
                    tg.create_task(self._watch_input_stall())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_background_monitor())
                    tg.create_task(self._run_sleep_watch())
                    tg.create_task(self._run_proactive_mode())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled).
                    # Skipped in wake-word mode: it comes up asleep, and a briefing
                    # would mean talking while "asleep".
                    if not self._briefing_sent and get_brief_enabled() and self._awake:
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                def details(exc):
                    if isinstance(exc, BaseExceptionGroup):
                        return " ".join(details(child) for child in exc.exceptions)
                    return f"{type(exc).__name__}: {exc}"
                err_str = details(e)
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                # Voluntary reconnect (voice change) — not an error. Rebuild the
                # session immediately with no backoff and no scary logs.
                if _is_reconnect_signal(e):
                    runtime_event("live_reconnect_requested", reason=getattr(self, "_reconnect_reason", "settings"))
                    print("[JARVIS] Voluntary reconnect requested.")
                    if not _keep_context_of(e):
                        # A deliberate clean slate (voice change) — drop the
                        # handle so the next connect really does start empty.
                        self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                # A resumption handle the server will not accept — expired, or
                # belonging to a session it has since dropped. Without this, the
                # same dead handle would be replayed on every retry and the
                # assistant would never come back at all: the feature meant to
                # survive a reconnect would be the thing preventing one. Drop it
                # once and let the next attempt start clean.
                if _resumed_with and (
                    "resum" in err_str.lower()
                    or "handle" in err_str.lower()
                    or "INVALID_ARGUMENT" in err_str
                    or "NOT_FOUND" in err_str
                ):
                    print("[JARVIS] 🔗 Resumption handle rejected — starting a fresh session")
                    self.ui.write_log("SYS: Could not restore the conversation — starting fresh.")
                    self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                runtime_event("live_session_error", error_types=",".join(type(child).__name__ for child in e.exceptions) if isinstance(e, BaseExceptionGroup) else type(e).__name__)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Turn-taking / media / thinking knobs rejected by the server
                # (preview API drift) — drop them first, because they are the
                # newest fields and the cheapest to lose. Proactive audio is
                # tried again on the next pass if the error persists.
                if self._tuned_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                    or "realtime_input" in err_str.lower()
                    or "media_resolution" in err_str.lower()
                    or "thinking" in err_str.lower()
                ):
                    self._tuned_live = False
                    print("[JARVIS] Live tuning rejected — reconnecting without it.")
                    continue


                # Proactive audio rejected by the server (preview API drift) —
                # drop it and reconnect with the plain config.
                if self._enhanced_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "proactiv" in err_str.lower()
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                ):
                    self._enhanced_live = False
                    self.ui.write_log(
                        "SYS: Proactive audio unavailable — reconnecting without it."
                    )
                    continue

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str or "API_KEY_INVALID" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Connection failed — retrying in {_conn_backoff}s. "
                        "(a VPN may be required)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None
                self._interrupted = False
                self._response_pending = False
                # Only save if there was a real conversation (≥3 turns)
                if len(self._session_log) >= 3:
                    asyncio.create_task(self._save_session_summary())

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def main():
    ui = JarvisUI("face.png")
    from core.runtime_diagnostics import set_reporter
    set_reporter(ui.write_log)

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")
        except Exception:
            traceback.print_exc()
            try:
                ui.write_log("ERR: Assistant stopped unexpectedly. See ~/.local/state/ArienX/runtime.log and restart the app.")
            except RuntimeError:
                pass  # the Qt window may already have closed
        finally:
            jarvis.session = None
            jarvis._loop = None
            jarvis._reconnect_event = None

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
