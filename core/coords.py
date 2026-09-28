"""Canonical screen-coordinate layer for computer control.

One measured conversion path, used by every action that turns a perceived
location into a mouse target:

    model/vision space (0-1000 relative)
        → screenshot pixels (what the model actually saw)
        → pointer space (where pyautogui must click)

Never assumes screenshot pixels equal mouse coordinates: HiDPI/scaling can
disagree, so both ends are measured per call and the mapping is logged with
a [Computer] line for diagnostics. Pure functions where possible so the
math is unit-testable without a display; display access is lazy and raises
honest errors instead of guessing.
"""

from __future__ import annotations


def pointer_size() -> tuple[int, int]:
    """Live pointer space (pyautogui coordinates). Raises honestly."""
    try:
        import pyautogui
    except ImportError:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")
    w, h = pyautogui.size()
    return int(w), int(h)


def mouse_position() -> tuple[int, int]:
    try:
        import pyautogui
    except ImportError:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")
    x, y = pyautogui.position()
    return int(x), int(y)


def monitor_info() -> list[dict]:
    """Physical monitors via mss (best effort; [] when unavailable)."""
    try:
        import mss
        with mss.mss() as sct:
            return [dict(m) for m in sct.monitors[1:]]
    except Exception:
        return []


def rel_to_pointer(x1000: int, y1000: int, shot_w: int, shot_h: int,
                   ptr_w: int, ptr_h: int) -> tuple[int, int]:
    """0-1000 relative → pointer space, via the actual screenshot size.

    Same two-stage mapping screen_find always used (relative → shot pixels
    → pointer pixels), factored out so every locator shares it. Clamps into
    pointer space; a target outside the screen is never clickable.
    """
    try:
        x = int(x1000 * max(1, shot_w) / 1000)
        y = int(y1000 * max(1, shot_h) / 1000)
        x = int(x * max(1, ptr_w) / max(1, shot_w))
        y = int(y * max(1, ptr_h) / max(1, shot_h))
    except Exception:
        x, y = int(x1000), int(y1000)
    x = max(0, min(max(0, ptr_w - 1), x))
    y = max(0, min(max(0, ptr_h - 1), y))
    return x, y


def box_center_to_pointer(x1: int, y1: int, x2: int, y2: int,
                          shot_w: int, shot_h: int,
                          ptr_w: int, ptr_h: int) -> tuple[int, int]:
    """Bounding-box (0-1000) center → pointer space."""
    xa, xb = sorted((int(x1), int(x2)))
    ya, yb = sorted((int(y1), int(y2)))
    cx = (xa + xb) // 2
    cy = (ya + yb) // 2
    return rel_to_pointer(cx, cy, shot_w, shot_h, ptr_w, ptr_h)


def describe_mapping(shot_w: int, shot_h: int, ptr_w: int, ptr_h: int,
                     target: tuple[int, int] | None = None) -> str:
    """One-line diagnostic of the active mapping (log, never secrets)."""
    base = (f"screenshot={shot_w}x{shot_h} pointer-space={ptr_w}x{ptr_h}")
    if shot_w != ptr_w or shot_h != ptr_h:
        base += (f" scale=({ptr_w/max(1, shot_w):.3f},"
                 f"{ptr_h/max(1, shot_h):.3f})")
    else:
        base += " scale=1:1"
    if target is not None:
        base += f" target={target}"
    return base
