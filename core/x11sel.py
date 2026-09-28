"""X11 PRIMARY-selection owner + reader (no xclip/xsel needed).

Why this exists: on ibus-managed desktops, XTEST synthetic KeyPress events
never reach input contexts (verified: xev receives them, GTK/Tk/Qt entries
do not), and pyperclip has no backend without xclip/xsel. The remaining
universally-working text path is the X11 selection mechanism itself:

- WRITE: own PRIMARY, middle-click the already-focused field (ButtonPress
  events are delivered fine). Exact bytes, layout-independent, Unicode-safe.
- READ: request PRIMARY (the current mouse selection) — enables copy
  without Ctrl+C.

Only python-xlib is used (already required by pyautogui). All X I/O runs
on the owner thread; callers use set_text()/read() from any thread. Never
raises out of the public helpers — failures return honestly.
"""

from __future__ import annotations

import queue
import select
import threading
import time

_TARGETS = ("TARGETS", "TIMESTAMP", "UTF8_STRING", "STRING", "TEXT",
            "COMPOUND_TEXT", "SAVE_TARGETS", "ATOM", "INTEGER", "PRIMARY")


class PrimaryOwner(threading.Thread):
    """Owns PRIMARY and serves conversion requests until stop().

    Protocol notes (ICCCM, learned the hard way):
    - converted data goes on the REQUESTOR's window, never our own;
    - TIMESTAMP/SAVE_TARGETS must succeed (empty is fine), unknown targets
      get NONE so the requestor falls back instead of hanging;
    - ownership is re-asserted on every set_text (focus clicks steal it).
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="PrimaryOwner")
        self._q: queue.Queue = queue.Queue()
        self._text = b""
        self._errors: list[str] = []
        self._ready = threading.Event()
        self._wid: int | None = None

    # -- caller side (any thread) -------------------------------------
    def set_text(self, text: str) -> None:
        self._q.put(text.encode("utf-8", "replace"))

    def holds(self) -> bool:
        """True if we currently own PRIMARY (separate connection check)."""
        try:
            from Xlib.display import Display
            d = Display()
            o = d.get_selection_owner(d.get_atom("PRIMARY"))
            ok = hasattr(o, "id") and self._wid is not None and o.id == self._wid
            d.close()
            return bool(ok)
        except Exception:
            return False

    def stop(self) -> None:
        self._q.put(None)

    # -- owner side (this thread only does X I/O) ----------------------
    def run(self) -> None:
        try:
            from Xlib import X
            from Xlib.display import Display
            from Xlib.protocol import event as Xev
        except Exception as e:
            self._errors.append(f"import: {e}")
            return
        try:
            d = Display()
            A = {n: d.get_atom(n) for n in _TARGETS}
            win = d.screen().root.create_window(0, 0, 1, 1, 0, 0)
            self._display, self._atoms, self._win = d, A, win
            self._wid = win.id
            self._fd = d.fileno()
            self._ready.set()
        except Exception as e:
            self._errors.append(f"init: {e}")
            return
        while True:
            try:
                r, _, _ = select.select([self._fd], [], [], 0.05)
            except Exception:
                continue
            try:
                while True:
                    item = self._q.get_nowait()
                    if item is None:
                        return
                    self._text = item
                    win.set_selection_owner(A["PRIMARY"], X.CurrentTime)
                    d.flush()
            except queue.Empty:
                pass
            try:
                while d.pending_events():
                    ev = d.next_event()
                    if ev.type != X.SelectionRequest:
                        continue
                    try:
                        req = d.create_resource_object("window", ev.requestor)
                        prop = ev.property
                        if ev.target == A["TARGETS"]:
                            req.change_property(
                                prop, A["ATOM"], 32,
                                [A["TARGETS"], A["TIMESTAMP"],
                                 A["UTF8_STRING"], A["STRING"]])
                        elif ev.target in (A["UTF8_STRING"], A["STRING"]):
                            req.change_property(prop, ev.target, 8, self._text)
                        elif ev.target == A["TIMESTAMP"]:
                            req.change_property(prop, A["INTEGER"], 32, [0])
                        elif ev.target == A["SAVE_TARGETS"]:
                            pass
                        else:
                            prop = X.NONE
                        n = Xev.SelectionNotify(
                            time=ev.time, requestor=ev.requestor,
                            selection=ev.selection, target=ev.target,
                            property=prop)
                        d.send_event(ev.requestor, n, 0, 0)
                        d.flush()
                    except Exception as e:
                        self._errors.append(f"serve: {e}")
            except Exception:
                pass


_owner: PrimaryOwner | None = None
_owner_lock = threading.Lock()


def get_owner() -> PrimaryOwner | None:
    """Process-wide singleton owner (started on first use). None if X fails."""
    global _owner
    with _owner_lock:
        if _owner is not None:
            return _owner if _owner.is_alive() else None
        try:
            o = PrimaryOwner()
            o.start()
            if not o._ready.wait(timeout=5.0):
                return None
            _owner = o
            return o
        except Exception:
            return None


def read_primary(timeout: float = 3.0) -> str | None:
    """Read the current PRIMARY selection (None when empty/unavailable)."""
    try:
        from Xlib import X
        from Xlib.display import Display
    except Exception:
        return None
    try:
        d = Display()
        PRIM = d.get_atom("PRIMARY")
        UTF8 = d.get_atom("UTF8_STRING")
        if not hasattr(d.get_selection_owner(PRIM), "id"):
            d.close()
            return None
        win = d.screen().root.create_window(0, 0, 1, 1, 0, 0)
        prop = d.intern_atom("ARIENX_READ")
        win.convert_selection(PRIM, UTF8, prop, X.CurrentTime)
        d.flush()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            while d.pending_events():
                ev = d.next_event()
                if (ev.type == X.SelectionNotify
                        and ev.property != X.NONE):
                    v = win.get_full_property(prop, 0)
                    d.close()
                    if v is None or v.format != 8:
                        return None
                    return bytes(v.value).decode("utf-8", "replace")
            time.sleep(0.02)
        d.close()
        return None
    except Exception:
        return None
