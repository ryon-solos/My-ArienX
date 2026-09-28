#computer_control.py
import io
import json
import platform
import re
import string
import subprocess
import sys

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}
import time
import random
from pathlib import Path

from core import coords as _coords

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE         = _base_dir()
_CONFIG_PATH  = _BASE / "config" / "api_keys.json"
_MEMORY_PATH  = _BASE / "memory" / "long_term.json"

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _platform_os() -> str:
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def _get_os() -> str:
    return _load_config().get("os_system", _platform_os()).lower()


def _get_api_key() -> str:
    return _load_config().get("gemini_api_key", "")

_SAFE_SCREENSHOT_ROOTS = (
    Path.home(),
)

def _safe_screenshot_path(requested: str | None) -> Path:
    fallback = Path.home() / "Desktop" / "jarvis_screenshot.png"
    if not requested:
        return fallback
    try:
        p = Path(requested).expanduser().resolve()
        for root in _SAFE_SCREENSHOT_ROOTS:
            if p.is_relative_to(root.resolve()):
                p.parent.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:
        pass
    return fallback

def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")

_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson",
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def _random_data(data_type: str) -> str:
    dt = data_type.lower().strip()

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    if dt == "last_name":
        return random.choice(_LAST_NAMES)

    if dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    if dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    if dt == "username":
        return f"{random.choice(_FIRST_NAMES).lower()}{random.randint(100, 9999)}"

    if dt == "password":
        chars = string.ascii_letters + string.digits + "!@#$%"
        raw   = (
            random.choice(string.ascii_uppercase)
            + random.choice(string.digits)
            + random.choice("!@#$%")
            + "".join(random.choices(chars, k=9))
        )
        return "".join(random.sample(raw, len(raw)))

    if dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1_000_000, 9_999_999)}"

    if dt == "birthday":
        y = random.randint(1980, 2000)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{m:02d}/{d:02d}/{y}"

    if dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    if dt == "zip_code":
        return str(random.randint(10000, 99999))

    if dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000, 9999)}"

def _user_profile() -> dict:
    """Read identity fields from long-term memory."""
    try:
        if _MEMORY_PATH.exists():
            data     = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {k: v.get("value", "") for k, v in identity.items()}
    except Exception:
        pass
    return {}

def _type(text: str, interval: float = 0.03) -> str:
    """Type short text via synthesized keys.

    Works for XIM-less targets and WM-level input. In ibus-managed text
    fields synthetic keys are eaten — callers that need guarantees should
    use _paste_at_point() (selection+middle-click) instead.
    """
    _require_pyautogui()
    _log_keyboard("xtype", text)
    time.sleep(0.3)
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _log_keyboard(method: str, text: str, extra: str = "") -> None:
    """[Keyboard] line: method + lengths + focus. Never the text itself."""
    try:
        focus = _active_window()
    except Exception:
        focus = "unknown"
    print(f"[Keyboard] method={method} text_length={len(text or '')} "
          f"focused_window={focus!r} {extra}".rstrip())


# Last pointer-placed interaction, for paste-at-point flows: middle-click
# pastes PRIMARY where the pointer is, so pasting exactly where the model
# just left-clicked keeps the paste inside the intended field.
_last_point: tuple[int, int] | None = None


def _remember_point(x: int | None, y: int | None) -> None:
    global _last_point
    try:
        if x is not None and y is not None:
            _last_point = (int(x), int(y))
    except Exception:
        pass


def _paste_at_point(text: str, timeout: float = 8.0) -> str:
    """Exact native text insertion: PRIMARY ownership + middle-click.

    Bypasses key-event input entirely (ibus eats synthetic keys), so it is
    layout-independent and Unicode-safe. Only ever middle-clicks the point
    of the session's last left-click (a field the model already targeted).
    Returns a verified/unverified result via cheap screenshot diff.
    """
    from core import x11sel
    if not text:
        return "Nothing to paste."
    if _last_point is None:
        return "NO_POINT"
    x, y = _last_point
    owner = x11sel.get_owner()
    if owner is None:
        return "paste unavailable: X11 selection owner failed"
    # Occlusion gate: refuse a blind paste when the point is covered by a
    # different window than the focused one — the middle-click would land
    # in the wrong application (e.g. a fullscreen browser over a dialog).
    try:
        wid_at, title_at = _window_at(x, y)
        focus = _active_window()
        if (wid_at and title_at and focus not in ("unknown",)
                and title_at not in focus and focus not in title_at):
            return (f"paste refused: point ({x},{y}) is covered by "
                    f"'{title_at[:50]}' while '{focus[:50]}' is focused. "
                    f"Raise the target window first.")
    except Exception:
        pass
    before = _grab_small()
    owner.set_text(text)
    time.sleep(0.25)
    if not owner.holds():
        return "paste unverified: lost PRIMARY ownership before click"
    _require_pyautogui()
    import pyautogui as _pg
    _pg.click(x, y, button="middle")
    _log_keyboard("primary-middle", text, f"at=({x},{y})")
    time.sleep(0.4)
    after = _grab_small()
    if before is not None and after is not None and _shots_differ(before, after):
        return (f"Pasted {len(text)} chars at ({x},{y}) "
                f"(verified: screen changed).")
    return (f"Pasted {len(text)} chars at ({x},{y}) (unverified: no visible "
            f"change — target may not accept middle-click paste).")


def _type_verified(text: str, interval: float = 0.03) -> str:
    """XTEST typing + cheap change verification (honest either way)."""
    _require_pyautogui()
    before = _grab_small()
    _log_keyboard("xtype", text)
    time.sleep(0.2)
    import pyautogui as _pg
    _pg.typewrite(text, interval=interval)
    time.sleep(0.3)
    after = _grab_small()
    base = f"Typed: {text[:60]}{'…' if len(text) > 60 else ''}"
    if before is not None and after is not None and _shots_differ(before, after):
        return base + " (verified: screen changed)."
    return base + " (unverified: no visible change)."


def _atspi_type(text: str, replace: bool) -> str | None:
    """Exact native insertion via AT-SPI (no keys, no clipboard).

    Returns a result string on success, None when no usable editable is
    focused (caller falls through to the next mechanism). Readback decides
    verified/unverified — never claimed blindly.
    """
    try:
        from core import atspi as _a
    except Exception:
        return None
    if not _a.available() or not text:
        return None
    # The a11y tree can lag a fresh focus click by a moment; settle briefly
    # (bounded) before giving up to the next mechanism.
    acc = None
    for _ in range(3):
        acc = _a.focused_editable()
        if acc is not None:
            break
        time.sleep(0.5)
    if acc is None:
        # Fallback: the active window's app with exactly one visible
        # editable (e.g. a lone dialog entry). Bounded and unambiguous —
        # never guesses among several fields.
        try:
            import subprocess as _sp
            out = _sp.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                          capture_output=True, text=True, timeout=5).stdout
            import re as _re
            m = _re.search(r"0x[0-9a-fA-F]+", out or "")
            wid = m.group(0) if m else ""
            title = ""
            if wid and wid != "0x0":
                lst = _sp.run(["wmctrl", "-l"], capture_output=True,
                              text=True, timeout=5).stdout or ""
                for line in lst.splitlines():
                    parts = line.split(None, 3)
                    if not parts:
                        continue
                    try:
                        if int(parts[0], 16) != int(wid, 16):
                            continue
                    except Exception:
                        continue
                    title = parts[3] if len(parts) > 3 else ""
                    break
            if title:
                acc = _a.single_visible_editable(_a.find_app_by_window(title))
                if acc is not None:
                    print(f"[Keyboard] at-spi fallback: single editable in "
                          f"'{title[:40]}'")
        except Exception:
            pass
    if acc is None:
        return None
    label = _a.describe_acc(acc)
    if replace:
        ok, back = _a.set_entry_text(acc, text)
    else:
        ok, back = _a.insert_entry_text(acc, text)
    print(f"[Keyboard] method=at-spi-{('set' if replace else 'insert')} "
          f"text_length={len(text)} target={label!r}")
    if not ok:
        return (f"AT-SPI edit failed on {label} "
                f"(falling through).")
    if back is not None and text in back:
        return (f"Typed {len(text)} chars into {label} "
                f"(verified: readback matches).")
    return (f"Typed {len(text)} chars into {label} "
            f"(unverified: readback differs).")


def _smart_type(text: str, clear_first: bool = True) -> str:
    """Clear-then-insert with mouse-first clearing and exact insertion.

    Clearing via Ctrl+A fails under ibus-managed fields, so with a known
    point the field is selected with a triple-click (pure mouse) and the
    text is inserted via PRIMARY+middle-click (exact, Unicode-safe). Falls
    back to the legacy key paths otherwise. Bounded: one paste attempt,
    then at most one legacy attempt.
    """
    _require_pyautogui()
    # Exact path first: AT-SPI replaces content directly (clear_first is
    # inherent to set_text; insert only when explicitly appending).
    if clear_first:
        r = _atspi_type(text, replace=True)
        if r is not None and "verified" in r and "unverified" not in r:
            return f"Smart-typed (exact): {r}"
    if clear_first and _last_point is not None:
        x, y = _last_point
        import pyautogui as _pg
        try:
            _pg.click(x, y, clicks=3)
            time.sleep(0.2)
            print(f"[Keyboard] method=triple-click-clear at=({x},{y})")
        except Exception as e:
            print(f"[ComputerControl] ⚠️ triple-click clear failed: {e}")
    elif clear_first:
        _clear_field()
        time.sleep(0.1)

    if _last_point is not None:
        r = _paste_at_point(text)
        if r != "NO_POINT" and "verified" in r:
            return f"Smart-typed (exact): {r}"
        if r != "NO_POINT":
            # Paste path ran but unconfirmed: one legacy attempt, then stop.
            print(f"[ComputerControl] paste unconfirmed ({r[:60]}), one legacy retry")
            return _type_verified(text) + " [after unconfirmed paste]"
        # NO_POINT unreachable here, kept for clarity.

    if len(text) > 20 and _PYPERCLIP:
        # Clipboard path needs a working backend (xclip/xsel on Linux).
        # When it is missing, fall back to keystrokes rather than failing.
        try:
            pyperclip.copy(text)
            time.sleep(0.1)
            paste_key = "command" if _get_os() == "mac" else "ctrl"
            pyautogui.hotkey(paste_key, "v")
            return f"Smart-typed (clipboard): {text[:60]}{'…' if len(text) > 60 else ''}"
        except Exception as e:
            print(f"[ComputerControl] ⚠️ clipboard unavailable ({e}), typing directly")

    return _type_verified(text)


def _click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    _require_pyautogui()
    if x is not None and y is not None:
        pyautogui.click(x, y, button=button, clicks=clicks)
        return f"{'Double-c' if clicks == 2 else 'C'}licked ({x}, {y}) [{button}]"
    pyautogui.click(button=button, clicks=clicks)
    return f"Clicked at current position [{button}]"


def _hotkey(*keys) -> str:
    _require_pyautogui()
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str) -> str:
    _require_pyautogui()
    pyautogui.press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    _require_pyautogui()
    vertical   = direction in ("up", "down")
    clicks     = amount if direction in ("up", "right") else -amount
    pyautogui.scroll(clicks) if vertical else pyautogui.hscroll(clicks)
    return f"Scrolled {direction} ×{amount}"


def _move(x: int, y: int, duration: float = 0.3) -> str:
    _require_pyautogui()
    pyautogui.moveTo(x, y, duration=duration)
    return f"Mouse → ({x}, {y})"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    """Human-like drag: move → verify pointer → down → waypoints → up.

    A controlled waypoint path (not an instant jump) lets the desktop track
    the gesture; the pointer is verified at the start and the button is
    always released, including on exceptions.
    """
    _require_pyautogui()
    import pyautogui as _pg
    _remember_point(x2, y2)
    _pg.moveTo(x1, y1, duration=0.2)
    time.sleep(0.1)
    try:
        mx, my = _coords.mouse_position()
    except Exception:
        mx, my = x1, y1
    off = abs(mx - x1) + abs(my - y1)
    print(f"[Drag] start=({x1},{y1}) end=({x2},{y2}) duration={duration} "
          f"pointer_at=({mx},{my}) offset={off}")
    if off > 8:
        print(f"[Drag] ⚠️ pointer missed start by {off}px; continuing anyway")
    _pg.mouseDown(button="left")
    try:
        steps = max(2, min(12, int(duration / 0.08)))
        for i in range(1, steps + 1):
            wx = int(x1 + (x2 - x1) * i / steps)
            wy = int(y1 + (y2 - y1) * i / steps)
            _pg.moveTo(wx, wy, duration=max(0.01, duration / steps))
    finally:
        # Never leave the button held if the drag is interrupted.
        try:
            _pg.mouseUp(button="left")
        except Exception:
            pass
    print(f"[Drag] mouse_down=true mouse_up=true verified=pointer-path-done")
    return f"Dragged ({x1},{y1}) → ({x2},{y2})"


def _mouse_down(button: str = "left") -> str:
    _require_pyautogui()
    if button not in ("left", "right", "middle"):
        button = "left"
    import pyautogui as _pg
    _pg.mouseDown(button=button)
    return f"Mouse down [{button}]"


def _move_window(title: str, dx: int, dy: int,
                 duration: float = 0.6) -> str:
    """Move a window by (dx, dy) via Alt+Button1 drag from its titlebar.

    Why Alt+drag: on this desktop (Cinnamon/Muffin) a plain ButtonPress on
    window decorations from synthetic input does not reliably start a move
    grab (proven live), while Alt+Button1 anywhere in the window does —
    pixel-exact in testing. Geometry comes from wmctrl (exact, no vision);
    the move is verified by re-reading geometry afterwards.
    """
    _require_pyautogui()
    import pyautogui as _pg
    frag = (title or "").lower()
    match = None
    for wid, t, geom in _window_geometries():
        if frag and frag in t.lower():
            match = (wid, t, geom)
            break
    if match is None:
        # Fall back to focusing first (may restack/reveal the window).
        _focus_window(title)
        time.sleep(0.5)
        for wid, t, geom in _window_geometries():
            if frag and frag in t.lower():
                match = (wid, t, geom)
                break
    if match is None:
        return f"move_window: no window matching '{title}'."
    wid, t, geom = match
    try:
        gx, gy, gw, gh = (int(v) for v in geom.split(","))
    except Exception:
        return f"move_window: unreadable geometry for '{t}'."
    try:
        # Maximized windows cannot move: drop maximization first (stays
        # unmaximized afterwards — said plainly in the result).
        subprocess.run(["wmctrl", "-r", t, "-b",
                        "remove,maximized_vert,maximized_horz"],
                       capture_output=True, timeout=5)
        time.sleep(0.5)
        for w2, _t2, g2 in _window_geometries():
            if w2 == wid:
                try:
                    ng = [int(v) for v in g2.split(",")]
                    gx, gy, gw, gh = ng[0], ng[1], ng[2], ng[3]
                except Exception:
                    pass
    except Exception:
        pass
    _focus_window(title)
    time.sleep(0.3)
    tx, ty = gx + gw // 2, gy + 18
    _pg.moveTo(tx, ty, duration=0.2)
    time.sleep(0.15)
    _pg.keyDown("alt")
    time.sleep(0.1)
    try:
        _pg.mouseDown(button="left")
        try:
            steps = max(2, min(12, int(duration / 0.08)))
            for i in range(1, steps + 1):
                wx = int(tx + dx * i / steps)
                wy = int(ty + dy * i / steps)
                _pg.moveTo(wx, wy, duration=max(0.01, duration / steps))
        finally:
            try:
                _pg.mouseUp(button="left")
            except Exception:
                pass
    finally:
        try:
            _pg.keyUp("alt")
        except Exception:
            pass
    time.sleep(0.4)
    after = None
    for w2, _t2, g2 in _window_geometries():
        if w2 == wid:
            try:
                ag = [int(v) for v in g2.split(",")]
                after = (ag[0], ag[1])
            except Exception:
                pass
    print(f"[Drag] move_window '{t[:40]}' d=({dx},{dy}) "
          f"from=({gx},{gy}) to={after}")
    if after is None:
        return f"move_window '{t}': could not re-read geometry (unverified)."
    got = (after[0] - gx, after[1] - gy)
    if abs(got[0] - dx) + abs(got[1] - dy) <= 12:
        return (f"Moved window '{t}' by ({dx},{dy}) "
                f"(verified: geometry {gx},{gy} → {after[0]},{after[1]}; "
                f"window left unmaximized).")
    return (f"Moved window '{t}' by ({dx},{dy}) but geometry shows "
            f"{gx},{gy} → {after[0]},{after[1]} (unverified).")
    _require_pyautogui()
    if button not in ("left", "right", "middle"):
        button = "left"
    pyautogui.mouseDown(button=button)
    return f"Mouse down [{button}]"


def _mouse_up(button: str = "left") -> str:
    _require_pyautogui()
    if button not in ("left", "right", "middle"):
        button = "left"
    try:
        pyautogui.mouseUp(button=button)
    except Exception:
        pass
    return f"Mouse up [{button}] (released)"


def _key_down(key: str) -> str:
    _require_pyautogui()
    pyautogui.keyDown(key)
    return f"Key down: {key}"


def _key_up(key: str) -> str:
    _require_pyautogui()
    try:
        pyautogui.keyUp(key)
    except Exception:
        pass
    return f"Key up: {key} (released)"


def _select_all() -> str:
    _require_pyautogui()
    select_key = "command" if _get_os() == "mac" else "ctrl"
    pyautogui.hotkey(select_key, "a")
    return "Selected all"


def _get_state() -> str:
    """Cheap pre-action grounding: geometry + pointer + window, no model call.

    Answers 'where am I' before an uncertain action without spending a
    screenshot-to-Gemini round trip. Never raises; unknown parts say so.
    """
    parts = []
    try:
        w, h = _coords.pointer_size()
        mx, my = _coords.mouse_position()
        parts.append(f"pointer-space={w}x{h} mouse=({mx},{my})")
    except Exception as e:
        parts.append(f"pointer: unavailable ({e})")
    mons = _coords.monitor_info()
    if mons:
        parts.append("monitors=" + ", ".join(
            f"{m.get('width')}x{m.get('height')}+{m.get('left')}+{m.get('top')}"
            for m in mons))
    else:
        parts.append("monitors: unknown (single assumed)")
    parts.append(f"window: {_active_window()}")
    print(f"[Computer] state: {' | '.join(parts)}")
    return " | ".join(parts)


def _active_window() -> str:
    """Best-effort foreground window title (X11 via wmctrl/xprop; honest gaps)."""
    if _get_os() not in ("linux", "windows", "mac"):
        return "unknown"
    if _get_os() == "linux":
        try:
            prop = subprocess.run(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                capture_output=True, text=True, timeout=5,
            )
            m = re.search(r"0x[0-9a-fA-F]+", prop.stdout or "")
            if not m:
                return "unknown (xprop unavailable)"
            wid = m.group(0)
            if wid == "0x0":
                return "none (desktop)"
            lst = subprocess.run(
                ["wmctrl", "-l"], capture_output=True, text=True, timeout=5,
            )
            try:
                target = int(wid, 16)
            except Exception:
                target = None
            for line in (lst.stdout or "").splitlines():
                parts = line.split(None, 3)
                if not parts:
                    continue
                try:
                    if target is not None and int(parts[0], 16) == target:
                        return parts[3] if len(parts) > 3 else parts[0]
                except Exception:
                    if line.lower().startswith(wid.lower()):
                        return parts[3] if len(parts) > 3 else parts[0]
            return f"unknown ({wid})"
        except FileNotFoundError as e:
            return f"unknown (missing: {e.filename})"
        except Exception as e:
            return f"unknown ({e})"
    return "unknown (unsupported OS probe)"


def _clipboard_get() -> str:
    if _PYPERCLIP:
        return pyperclip.paste()
    _hotkey("ctrl", "c")
    time.sleep(0.2)
    return "(copied — pyperclip unavailable for read)"


def _clipboard_paste(text: str) -> str:
    if _PYPERCLIP:
        try:
            pyperclip.copy(text)
            time.sleep(0.1)
            _require_pyautogui()
            paste_key = "command" if _get_os() == "mac" else "ctrl"
            pyautogui.hotkey(paste_key, "v")
            return f"Pasted: {text[:60]}{'…' if len(text) > 60 else ''}"
        except Exception as e:
            print(f"[ComputerControl] ⚠️ clipboard unavailable ({e}), typing directly")
            _require_pyautogui()
            pyautogui.typewrite(text, interval=0.04)
            return f"Pasted (typed fallback): {text[:60]}{'…' if len(text) > 60 else ''}"
    return "pyperclip not available"


def _screenshot(save_path: str | None = None) -> str:
    _require_pyautogui()
    path = _safe_screenshot_path(save_path)
    img  = pyautogui.screenshot()
    img.save(str(path))
    return f"Screenshot saved: {path}"


def _clear_field() -> str:
    _require_pyautogui()
    select_key = "command" if _get_os() == "mac" else "ctrl"
    pyautogui.hotkey(select_key, "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    return "Field cleared"

def _focus_window(title: str) -> str:
    os_name = _get_os()

    if os_name == "windows":
        try:
            script = f'(New-Object -ComObject WScript.Shell).AppActivate("{title}")'
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=5, **_WIN_HIDE,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (Windows) failed: {e}"

    if os_name == "mac":
        script = (
            f'tell application "System Events" to '
            f'set frontmost of (first process whose name contains "{title}") to true'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (macOS) failed: {e}"

    if os_name == "linux":
        try:
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(0.3)
                if _window_listed(title):
                    return f"Focused window: {title} (verified: in window list)."
                return (f"Focused window: {title} (unverified: not found in "
                        f"window list after raise).")
        except FileNotFoundError:
            pass
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title, "windowactivate"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except FileNotFoundError:
            return "focus_window (Linux) requires wmctrl or xdotool"
        except Exception as e:
            return f"focus_window (Linux) failed: {e}"

    return f"focus_window: unknown OS '{os_name}'"

def _window_listed(title: str) -> bool:
    """True when a window matching `title` appears in `wmctrl -l` (X11)."""
    try:
        out = subprocess.run(
            ["wmctrl", "-l"], capture_output=True, text=True, timeout=5,
        )
        frag = (title or "").lower()
        return bool(frag) and frag in (out.stdout or "").lower()
    except Exception:
        return False


def _window_geometries() -> list[tuple[int, str, str]]:
    """All windows as (wid_int, title, geom) — geom 'x,y,w,h' or ''."""
    out: list[tuple[int, str, str]] = []
    try:
        proc = subprocess.run(
            ["wmctrl", "-lG"], capture_output=True, text=True, timeout=5,
        )
        for line in (proc.stdout or "").splitlines():
            parts = line.split(None, 7)
            if len(parts) < 7:
                continue
            try:
                wid = int(parts[0], 16)
                x, y, w, h = (int(parts[2]), int(parts[3]),
                              int(parts[4]), int(parts[5]))
            except Exception:
                continue
            title = parts[7] if len(parts) > 7 else ""
            out.append((wid, title, f"{x},{y},{w},{h}"))
    except Exception:
        pass
    return out


def _stacking_order() -> list[int]:
    """Window ids bottom-to-top (X11 _NET_CLIENT_LIST_STACKING)."""
    try:
        proc = subprocess.run(
            ["xprop", "-root", "_NET_CLIENT_LIST_STACKING"],
            capture_output=True, text=True, timeout=5,
        )
        return [int(v, 16) for v in
                re.findall(r"0x[0-9a-fA-F]+", proc.stdout or "")]
    except Exception:
        return []


def _window_at(x: int, y: int) -> tuple[int, str]:
    """Topmost window containing pointer point (wid_int, title).

    Returns (0, '') when nothing matches or the query fails. Used to refuse
    blind clicks/pastes into occluded targets: if the window under the
    point is not the focused window, the click would land elsewhere.
    """
    try:
        geos = {wid: (title, geom) for wid, title, geom in _window_geometries()}
        if not geos:
            return 0, ""
        order = _stacking_order() or list(geos)
        for wid in reversed(order):
            info = geos.get(wid)
            if not info:
                continue
            title, geom = info
            try:
                gx, gy, gw, gh = (int(v) for v in geom.split(","))
            except Exception:
                continue
            if gx <= x < gx + gw and gy <= y < gy + gh:
                return wid, title
    except Exception:
        pass
    return 0, ""


def _grab_small() -> bytes | None:
    """Tiny grayscale screenshot for change detection. Local and fast
    (~a few ms); no model call. None when capture is unavailable."""
    try:
        from PIL import Image
        img = pyautogui.screenshot().convert("L").resize((96, 54), Image.BILINEAR)
        return img.tobytes()
    except Exception:
        return None


def _shots_differ(a: bytes | None, b: bytes | None, thresh: float = 6.0) -> bool:
    """True when two captures differ meaningfully. A moved cursor alone
    (~20 px of 5184) stays far below the threshold; any real UI reaction
    clears it easily. Different sizes count as changed."""
    if not a or not b or len(a) != len(b):
        return True
    n = len(a)
    total = 0
    for x, y in zip(a, b):
        total += abs(x - y)
        if total > thresh * n:
            return True   # early exit, no need to scan the rest
    return (total / n) > thresh


def _wait_screen_change(before: bytes | None, tries: int = 5,
                        gap: float = 0.5) -> bytes | None:
    """Poll for a visible change (OS UI can react slowly on first open).
    Returns the changed capture, or None when nothing changed. Still only
    ONE click per attempt — this observes longer, it never re-clicks."""
    after = None
    for _ in range(max(1, tries)):
        time.sleep(gap)
        after = _grab_small()
        if after is not None and _shots_differ(before, after):
            return after
    return None


# Words suggesting the target genuinely lives at the screen edge (taskbar,
# dock, tray clock, window chrome, scrollbars). Anything else found within a
# few pixels of the edge is treated as a misread, not a click target.
_EDGE_HINTS = (
    "taskbar", "dock", "tray", "systray", "clock", "panel", "menu bar",
    "menubar", "title bar", "titlebar", "window button", "close button",
    "minimize", "maximize", "scrollbar", "scroll bar", "edge", "corner",
    "notification", "start menu", "start button", "system menu",
)
_EDGE_MARGIN = 8      # px from the border that counts as "at the edge"
_MIN_BOX_SIDE = 3     # px; anything smaller is a glyph/cursor artifact


def _screen_find(description: str) -> tuple[int, int] | None:
    """Locate a visible UI element, returning the CENTER of its clickable area.

    One fresh screenshot per call; bounding-box answer preferred (center is
    derived, which is far more stable than a model-guessed point for small
    targets). Returns None (NOT_FOUND/UNCERTAIN) instead of inventing a
    coordinate: out-of-bounds, degenerate, or edge-mismatched answers are
    rejected locally with no extra model call.
    """
    api_key = _get_api_key()
    if not api_key:
        print("[ComputerControl] ⚠️ No API key for screen_find")
        return None
    desc = (description or "").strip()
    print(f"[ScreenFind] target={desc[:60]!r}")

    try:
        from google import genai
        from google.genai import types as gtypes

        _require_pyautogui()
        w, h  = pyautogui.size()
        img   = pyautogui.screenshot()
        buf   = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        prompt = (
            f"This is a screenshot of a {w}×{h} pixel screen. "
            f"Locate the UI element described as: '{desc}'. "
            f"Reply with ONLY its tight bounding box as: x1,y1,x2,y2, where "
            f"coordinates are 0-1000 relative space (origin top-left, x to "
            f"the right, y downward, full screen spans 0 to 1000 on each "
            f"axis). Box the clickable control tightly, not its label or "
            f"surroundings. "
            f"If a box is impractical, reply with ONLY the center in the same "
            f"0-1000 space as: x,y "
            f"If the element is not visible on screen, reply: NOT_FOUND"
        )

        from core import gemini
        response = gemini.call(
            [gtypes.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt],
            tier=gemini.FAST, timeout_ms=20_000,
        )
        if response is None:
            print("[ScreenFind] result=NOT_FOUND (no model answer)")
            return None

        text = (response.text or "").strip()
        if "NOT_FOUND" in text.upper():
            print("[ScreenFind] result=NOT_FOUND (model)")
            return None

        nums = [int(v) for v in re.findall(r"-?\d+", text)][:4]
        if len(nums) == 4:
            x1, y1, x2, y2 = nums
            xa, xb = sorted((x1, x2))
            ya, yb = sorted((y1, y2))
        elif len(nums) == 2:
            xa, ya = nums
            xb, yb = xa, ya
        else:
            print(f"[ScreenFind] result=NOT_FOUND (unparseable: {text[:60]!r})")
            return None

        # Validate in model units, then map the center through the canonical
        # layer. (Box-size checks use pointer pixels, not 0-1000 units.)
        if len(nums) == 4:
            xa, xb = sorted((xa, xb))
            ya, yb = sorted((ya, yb))
            if xb <= xa or yb <= ya:
                print("[ScreenFind] result=NOT_FOUND (empty box)")
                return None
            try:
                sw, sh = img.size
                pw = (xb - xa) * w / 1000.0
                ph = (yb - ya) * h / 1000.0
            except Exception:
                pw = ph = 999.0
            if pw < _MIN_BOX_SIDE and ph < _MIN_BOX_SIDE:
                # A box smaller than a few pixels is a glyph, cursor, or
                # noise — never a button. (Point answers skip this: the
                # model gave no size.)
                print("[ScreenFind] result=NOT_FOUND (degenerate box)")
                return None
        at_edge = False
        try:
            sw, sh = img.size
            if len(nums) == 4:
                cx, cy = _coords.box_center_to_pointer(
                    xa, xb, ya, yb, sw, sh, w, h)
            else:
                cx, cy = _coords.rel_to_pointer(xa, ya, sw, sh, w, h)
            print(f"[Computer] {_coords.describe_mapping(sw, sh, w, h, (cx, cy))}")
            at_edge = (cx < _EDGE_MARGIN or cy < _EDGE_MARGIN
                       or cx > w - 1 - _EDGE_MARGIN or cy > h - 1 - _EDGE_MARGIN)
        except Exception:
            cx, cy = (xa + xb) // 2, (ya + yb) // 2
        if at_edge and not any(k in desc.lower() for k in _EDGE_HINTS):
            print(f"[ScreenFind] result=NOT_FOUND (edge {cx},{cy} without edge target)")
            return None

        print(f"[ScreenFind] found=({cx},{cy}) validation=OK")
        return cx, cy

    except Exception as e:
        print(f"[ComputerControl] ⚠️ screen_find failed: {e}")

    print("[ScreenFind] result=NOT_FOUND (error)")
    return None

# Keys whose values must never reach the console log: free-typed text can
# contain passwords or other secrets the user dictated.
_REDACT_KEYS = ("text", "keys", "key")


def _safe_params(params: dict) -> dict:
    """Log-safe copy: free-text values replaced by their length, long
    descriptions truncated. Structure (keys) stays for debugging."""
    safe: dict = {}
    for k, v in (params or {}).items():
        if k in _REDACT_KEYS:
            safe[k] = f"<{len(str(v))} chars>" if v else v
        elif isinstance(v, str) and len(v) > 60:
            safe[k] = v[:60] + "…"
        else:
            safe[k] = v
    return safe


def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for all computer control actions.

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      text          : text to type or paste
      x, y          : screen coordinates (pointer space; prefer screen_find)
      x1, y1, x2, y2: drag endpoints (pointer space)
      duration      : drag seconds (default: 0.5)
      button        : 'left' | 'right' | 'middle' (default: left)
      keys          : hotkey string, e.g. 'ctrl+c'
      key           : single key name, e.g. 'enter'
      direction     : 'up' | 'down' | 'left' | 'right'
      amount        : scroll amount (default: 3)
      seconds       : wait duration
      title         : window title fragment for focus_window/switch_window
      description   : natural-language element description for screen_find/click
      type          : data type for random_data
      field         : memory field name for user_data
      clear_first   : bool, clear field before typing (default: true)
      path          : save path for screenshot (must be inside home dir)

    Actions:
      type          — type text at cursor
      smart_type    — clear field + type (clipboard-backed, typed fallback)
      click         — left click
      double_click  — double left click
      right_click   — right click
      middle_click  — middle click
      mouse_down    — hold button (pair with mouse_up)
      mouse_up      — release button (always safe)
      move          — move mouse
      drag          — click-drag between two points
      hotkey        — key combination
      press         — single key
      key_down      — hold key (pair with key_up)
      key_up        — release key (always safe)
      select_all    — select-all (os-aware)
      scroll        — scroll the wheel
      copy          — read clipboard
      paste         — write + paste clipboard
      screenshot    — capture screen (safe path only)
      get_state     — geometry + pointer + window, no model call
      wait          — sleep N seconds
      clear_field   — select-all + delete
      focus_window  — bring window to foreground (verified on Linux)
      switch_window — alias of focus_window
      move_window   — move window by dx/dy via Alt+drag (verified geometry)
      screen_find   — AI element finder (returns x,y)
      screen_click  — AI element finder + click
      ui_find       — native UI tree lookup by app/role/name (exact coords)
      random_data   — generate fake form data
      user_data     — pull real data from memory
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return "No action specified for computer_control."

    if player:
        player.write_log(f"[Computer] {action}")

    print(f"[ComputerControl] ▶ {action}  {_safe_params(params)}")

    try:

        if action == "type":
            text = params.get("text", "")
            r = _atspi_type(text, replace=False)
            if r is not None and "verified" in r and "unverified" not in r:
                return r
            if r is not None:
                print(f"[ComputerControl] at-spi insert unconfirmed, legacy retry")
            return _type_verified(text)

        if action == "smart_type":
            return _smart_type(
                params.get("text", ""),
                clear_first=params.get("clear_first", True),
            )

        if action in ("click", "left_click"):
            x, y = params.get("x"), params.get("y")
            _remember_point(x, y)
            return _click(x, y, "left", 1)

        if action == "double_click":
            x, y = params.get("x"), params.get("y")
            _remember_point(x, y)
            return _click(x, y, "left", 2)

        if action == "right_click":
            x, y = params.get("x"), params.get("y")
            _remember_point(x, y)
            return _click(x, y, "right", 1)

        if action == "middle_click":
            _remember_point(params.get("x"), params.get("y"))
            return _click(params.get("x"), params.get("y"), "middle", 1)

        if action == "mouse_down":
            return _mouse_down(params.get("button", "left"))

        if action == "mouse_up":
            return _mouse_up(params.get("button", "left"))

        if action == "key_down":
            return _key_down(params.get("key", ""))

        if action == "key_up":
            return _key_up(params.get("key", ""))

        if action == "select_all":
            return _select_all()

        if action == "get_state":
            return _get_state()

        if action == "move":
            _remember_point(params.get("x", 0), params.get("y", 0))
            return _move(int(params.get("x", 0)), int(params.get("y", 0)))

        if action == "drag":
            return _drag(
                int(params.get("x1", params.get("x", 0))),
                int(params.get("y1", params.get("y", 0))),
                int(params.get("x2", 0)), int(params.get("y2", 0)),
                float(params.get("duration", 0.5)),
            )

        if action == "hotkey":
            raw  = params.get("keys", "")
            keys = [k.strip() for k in raw.split("+")] if isinstance(raw, str) else raw
            return _hotkey(*keys)

        if action == "press":
            return _press(params.get("key", "enter"))

        if action == "scroll":
            return _scroll(
                direction=params.get("direction", "down"),
                amount=int(params.get("amount", 3)),
            )

        if action == "copy":
            got = _clipboard_get()
            if got.startswith("(copied"):
                # pyperclip backend missing (no xclip/xsel): read the live
                # mouse selection directly instead of failing.
                from core import x11sel
                sel = x11sel.read_primary()
                if sel:
                    print("[Keyboard] method=primary-read "
                          f"text_length={len(sel)}")
                    return sel
            return got

        if action == "paste":
            text = params.get("text", "")
            r = _atspi_type(text, replace=True)
            if r is not None and "verified" in r and "unverified" not in r:
                return r
            r = _paste_at_point(text)
            if r != "NO_POINT":
                return r
            return _clipboard_paste(text)

        if action == "ui_find":
            try:
                from core import atspi as _a
            except Exception:
                return "ui_find unavailable: at-spi bridge failed to load"
            if not _a.available():
                return "ui_find unavailable: pyatspi missing or no a11y bus"
            items = _a.find_elements(
                params.get("app", ""), params.get("name", ""),
                params.get("role", ""),
                limit=int(params.get("limit", 10)))
            if not items:
                # The registry cache is transiently stale on some desktops;
                # one settle-and-retry before reporting honestly.
                time.sleep(0.8)
                items = _a.find_elements(
                    params.get("app", ""), params.get("name", ""),
                    params.get("role", ""),
                    limit=int(params.get("limit", 10)))
            if not items:
                return (f"No visible UI element matching "
                        f"app={params.get('app', '')!r} "
                        f"name={params.get('name', '')!r} "
                        f"role={params.get('role', '')!r}.")
            lines = [f"UI elements (verified live query, screen coords):"]
            for i, it in enumerate(items, 1):
                lines.append(
                    f"{i}. [{it['role']}] '{it['name']}' "
                    f"at ({it['x']},{it['y']}) size {it['w']}x{it['h']}")
            print(f"[Computer] ui_find app={params.get('app', '')!r} "
                  f"hits={len(items)}")
            return "\n".join(lines)

        if action == "screenshot":
            return _screenshot(params.get("path"))

        if action == "screen_find":
            coords = _screen_find(params.get("description", ""))
            return f"{coords[0]},{coords[1]}" if coords else "NOT_FOUND"

        if action == "screen_click":
            desc   = params.get("description", "")
            coords = _screen_find(desc)
            if not coords:
                return f"Element not found on screen: '{desc}'"
            _remember_point(coords[0], coords[1])
            before = _grab_small()
            if before is None:
                time.sleep(0.2)
                _click(x=coords[0], y=coords[1])
                return f"Clicked '{desc}' at {coords} (unverified: capture unavailable)."
            time.sleep(0.2)
            _click(x=coords[0], y=coords[1])
            _focus_note = ""
            try:
                _wid_at, _title_at = _window_at(coords[0], coords[1])
                _focus_now = _active_window()
                if (_title_at and _focus_now not in ("unknown",)
                        and _title_at not in _focus_now
                        and _focus_now not in _title_at):
                    _focus_note = (f" WARNING: point is under '{_title_at[:40]}' "
                                   f"but '{_focus_now[:40]}' is focused — the click "
                                   f"may have landed in the wrong window.")
                    print(f"[Computer] occlusion-mismatch{_focus_note}")
            except Exception:
                pass
            if _wait_screen_change(before) is not None:
                return (f"Clicked '{desc}' at {coords} (verified: screen "
                        f"changed).{_focus_note}")
            # No visible effect — re-aim once on a fresh find, never blindly
            # re-click the same spot.
            print(f"[ScreenFind] recovery=re-find target={desc[:60]!r}")
            coords2 = _screen_find(desc)
            if not coords2:
                return (f"Clicked '{desc}' at {coords}; the element is no longer "
                        f"found (unverified).")
            print(f"[ScreenFind] recovery_target={coords2}")
            if abs(coords2[0] - coords[0]) + abs(coords2[1] - coords[1]) <= 30:
                return (f"Clicked '{desc}' at {coords}, but nothing visibly "
                        f"changed — not retried on the same spot.")
            _remember_point(coords2[0], coords2[1])
            before2 = _grab_small()
            _click(x=coords2[0], y=coords2[1])
            if before2 is not None and _wait_screen_change(before2) is not None:
                return f"Clicked '{desc}' at {coords2} on retry (verified: screen changed)."
            return (f"Clicked '{desc}' at {coords2} on retry, but no visible "
                    f"change resulted.")

        if action == "wait":
            secs = float(params.get("seconds", 1.0))
            secs = min(secs, 30.0)
            time.sleep(secs)
            return f"Waited {secs}s"

        if action == "clear_field":
            return _clear_field()

        if action == "focus_window":
            return _focus_window(params.get("title", ""))

        if action == "switch_window":
            # Same mechanism, task language differs ("switch to Firefox").
            return _focus_window(params.get("title", ""))

        if action == "move_window":
            return _move_window(
                params.get("title", ""),
                int(params.get("dx", 100)), int(params.get("dy", 100)),
                float(params.get("duration", 0.6)),
            )

        if action == "random_data":
            dt     = params.get("type", "name")
            result = _random_data(dt)
            print(f"[ComputerControl] 🎲 random {dt} → {result}")
            return result

        if action == "user_data":
            field   = params.get("field", "name")
            profile = _user_profile()
            value   = profile.get(field, "")
            if not value:
                value = _random_data(field)
                print(f"[ComputerControl] ⚠️ No '{field}' in memory, using random: {value}")
            return value

        return f"Unknown action: '{action}'"

    except Exception as e:
        print(f"[ComputerControl] ❌ {action}: {e}")
        return f"computer_control '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_control",
    "description": "Direct OS-level screen/mouse/keyboard control OUTSIDE the web browser. Loop: get_state (cheap, no model) → screen_find/screen_click to locate by description (0-1000-free: pass words, get pointer pixels back, HiDPI measured) → act → result says verified/unverified. TEXT ENTRY: type/paste insert at the last clicked point via exact selection-paste (layout/Unicode-safe), verified by screen change; plain type uses synthesized keys (works for XIM-less targets and window-manager input — under ibus-managed fields synthetic keys may not register, so results say verified/unverified honestly). HOTKEYS reach window-manager bindings and plain targets; key combos into IME-managed text fields can be eaten by the input method — for text prefer type/paste, for buttons prefer screen_click. Drags run a controlled waypoint path with pointer verification and guaranteed release; move whole WINDOWS with move_window (Alt+drag, geometry-verified), not titlebar drags. Prefer locating by description over raw x,y; after any failure re-identify on a fresh screenshot, never blindly re-click. Do NOT use for actions inside a web page (→ browser_control); system settings (→ computer_settings); opening apps (→ open_app).",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "type | smart_type | click | double_click | right_click | middle_click | mouse_down | mouse_up | key_down | key_up | select_all | move | drag | hotkey | press | scroll | copy | paste | screenshot | get_state | wait | clear_field | focus_window | switch_window | move_window | screen_find | screen_click | ui_find | random_data | user_data"
            },
            "text": {
                "type": "STRING",
                "description": "Text to type or paste"
            },
            "x": {
                "type": "INTEGER",
                "description": "X coordinate"
            },
            "y": {
                "type": "INTEGER",
                "description": "Y coordinate"
            },
            "x1": {
                "type": "INTEGER",
                "description": "Drag start X (pointer space)"
            },
            "y1": {
                "type": "INTEGER",
                "description": "Drag start Y (pointer space)"
            },
            "x2": {
                "type": "INTEGER",
                "description": "Drag end X (pointer space)"
            },
            "y2": {
                "type": "INTEGER",
                "description": "Drag end Y (pointer space)"
            },
            "duration": {
                "type": "NUMBER",
                "description": "Drag seconds (default: 0.5)"
            },
            "button": {
                "type": "STRING",
                "description": "left | right | middle (default: left)"
            },
            "keys": {
                "type": "STRING",
                "description": "Key combination e.g. 'ctrl+c'"
            },
            "key": {
                "type": "STRING",
                "description": "Single key e.g. 'enter'"
            },
            "direction": {
                "type": "STRING",
                "description": "up | down | left | right"
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount (default: 3)"
            },
            "seconds": {
                "type": "NUMBER",
                "description": "Seconds to wait"
            },
            "title": {
                "type": "STRING",
                "description": "Window title for focus_window/switch_window/move_window"
            },
            "dx": {
                "type": "INTEGER",
                "description": "move_window X offset in pixels"
            },
            "dy": {
                "type": "INTEGER",
                "description": "move_window Y offset in pixels"
            },
            "description": {
                "type": "STRING",
                "description": "Element description for screen_find/screen_click"
            },
            "app": {
                "type": "STRING",
                "description": "App name fragment for ui_find (e.g. 'nemo', 'zenity')"
            },
            "name": {
                "type": "STRING",
                "description": "Element name fragment for ui_find"
            },
            "role": {
                "type": "STRING",
                "description": "Element role for ui_find (e.g. entry, button, menu item)"
            },
            "limit": {
                "type": "INTEGER",
                "description": "Max ui_find hits (default: 10)"
            },
            "type": {
                "type": "STRING",
                "description": "Data type for random_data"
            },
            "field": {
                "type": "STRING",
                "description": "Field for user_data: name|email|city"
            },
            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing (default: true)"
            },
            "path": {
                "type": "STRING",
                "description": "Save path for screenshot"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": computer_control,
}
