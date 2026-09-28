"""Minimal AT-SPI bridge for exact native UI grounding + text entry.

Why: on ibus-managed desktops XTEST synthetic KeyPress events never reach
input contexts (proven live: xev receives them, GTK/Tk/Qt entries do not),
pyperclip has no backend without xclip/xsel, and /dev/uinput is root-only.
AT-SPI drives the accessibility bus directly — no keys, no clipboard, no
focus races — with readback verification built in.

Surface kept tiny on purpose: find app → find visible editable/element →
act. Everything is best-effort and never raises; callers fall through to
the next input mechanism on any failure. pyatspi is imported lazily so a
missing/broken bus only disables this layer, never the app.
"""

from __future__ import annotations

import time

try:
    import pyatspi
    _ATSPI = True
except Exception:
    pyatspi = None  # type: ignore
    _ATSPI = False

_EDIT_ROLES: tuple = ()
_FIND_DEPTH = 16  # document views nest deep (xed text ~12); still bounded


def _roles():
    global _EDIT_ROLES
    if not _EDIT_ROLES and _ATSPI:
        _EDIT_ROLES = (pyatspi.ROLE_ENTRY, pyatspi.ROLE_TEXT,
                       pyatspi.ROLE_PASSWORD_TEXT)
    return _EDIT_ROLES


def available() -> bool:
    return _ATSPI


def _desktop():
    return pyatspi.Registry.getDesktop(0)


def find_app(name_frag: str):
    """First application whose name contains `name_frag` (case-insensitive).

    Retried: the registry cache is transiently stale on some desktops and a
    just-opened app may miss the first enumeration.
    """
    if not _ATSPI or not name_frag:
        return None
    frag = name_frag.lower()
    for _ in range(3):
        try:
            d = _desktop()
            for i in range(d.childCount):
                try:
                    a = d.getChildAtIndex(i)
                    if frag in (a.name or "").lower():
                        return a
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.5)
    return None


def find_app_by_window(title_frag: str):
    """App owning a visible window whose title contains `title_frag`."""
    if not _ATSPI or not title_frag:
        return None
    frag = title_frag.lower()
    for _ in range(3):
        try:
            d = _desktop()
            for i in range(d.childCount):
                try:
                    app = d.getChildAtIndex(i)
                except Exception:
                    continue
                hits = _walk(app, lambda a: (
                    a.getRoleName().lower() in ("window", "frame",
                                                "dialog", "alert")
                    and frag in (a.name or "").lower()), limit=1)
                if hits:
                    return app
        except Exception:
            pass
        time.sleep(0.5)
    return None


def single_visible_editable(app) -> object | None:
    """The editable iff the app exposes exactly one visible editable."""
    if not _ATSPI or app is None:
        return None
    hits = _walk(app, lambda a: (
        a.getRole() in _roles()
        and pyatspi.STATE_VISIBLE in _states(a)), limit=2)
    return hits[0] if len(hits) == 1 else None


def _walk(acc, pred, depth: int = 0, out: list | None = None,
          limit: int = 8) -> list:
    if out is None:
        out = []
    if acc is None or depth > _FIND_DEPTH or len(out) >= limit:
        return out
    try:
        if pred(acc):
            out.append(acc)
            if len(out) >= limit:
                return out
    except Exception:
        pass
    try:
        n = acc.childCount
    except Exception:
        return out
    for i in range(n):
        try:
            _walk(acc.getChildAtIndex(i), pred, depth + 1, out, limit)
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def _states(acc) -> tuple:
    try:
        return tuple(acc.getState().getStates())
    except Exception:
        return ()


def focused_editable():
    """The system-wide focused, visible, editable text object (or None)."""
    if not _ATSPI:
        return None
    try:
        d = _desktop()
        for i in range(d.childCount):
            try:
                app = d.getChildAtIndex(i)
            except Exception:
                continue
            hits = _walk(app, lambda a: (
                a.getRole() in _roles()
                and pyatspi.STATE_VISIBLE in _states(a)
                and pyatspi.STATE_FOCUSED in _states(a)), limit=1)
            if hits:
                return hits[0]
    except Exception:
        pass
    return None


def find_elements(app_name: str, name_sub: str = "", role: str = "",
                  visible_only: bool = True, limit: int = 10) -> list[dict]:
    """Visible native elements as [{role, name, x, y, w, h}]. No model call."""
    if not _ATSPI:
        return []
    app = find_app(app_name)
    if app is None:
        return []
    want = (name_sub or "").lower()
    out: list[dict] = []

    def pred(a) -> bool:
        try:
            if role and a.getRoleName().lower() != role.lower():
                return False
            if want and want not in (a.name or "").lower():
                return False
            if visible_only and pyatspi.STATE_VISIBLE not in _states(a):
                return False
            return True
        except Exception:
            return False

    for acc in _walk(app, pred, limit=limit):
        try:
            comp = acc.queryComponent()
            x, y = comp.getPosition(pyatspi.DESKTOP_COORDS)
            w, h = comp.getSize()
            # Unmapped/offscreen objects report INT_MIN — not actionable.
            if x <= -1000000 or y <= -1000000 or w <= 0 or h <= 0:
                continue
            out.append({"role": acc.getRoleName(), "name": acc.name or "",
                        "x": int(x), "y": int(y), "w": int(w), "h": int(h)})
        except Exception:
            continue
    return out


def entry_text(acc) -> str | None:
    try:
        return acc.queryText().getText(0, -1)
    except Exception:
        return None


def set_entry_text(acc, text: str) -> tuple[bool, str | None]:
    """Replace content exactly; returns (ok, readback_or_None)."""
    try:
        acc.queryEditableText().setTextContents(text)
        time.sleep(0.3)
        return True, entry_text(acc)
    except Exception as e:
        print(f"[Computer] at-spi set_text failed: {e}")
        return False, None


def insert_entry_text(acc, text: str) -> tuple[bool, str | None]:
    """Insert at caret (append semantics for `type`); (ok, readback)."""
    try:
        et = acc.queryEditableText()
        try:
            caret = int(et.caretOffset)
        except Exception:
            try:
                caret = int(et.queryText().characterCount)
            except Exception:
                caret = 0
        et.insertText(caret, text, len(text))
        time.sleep(0.3)
        return True, entry_text(acc)
    except Exception as e:
        print(f"[Computer] at-spi insert failed: {e}")
        return False, None


def describe_acc(acc) -> str:
    try:
        return f"{acc.getRoleName()} '{(acc.name or '')[:40]}'"
    except Exception:
        return "unknown element"
