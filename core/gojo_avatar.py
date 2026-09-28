"""
Gojo portrait avatar for the HUD centre — a drop-in stand-in for HoloAvatar.

Why this exists
--------------
The mesh avatar (core/avatar.py) renders measured human geometry: a realistic
wireframe/shaded head driven by facial landmarks. An anime look — spiky hair,
blindfold, stylised proportions — cannot be textured onto that mesh without
rebuilding the renderer, which is explicitly out of scope. So this module
implements the *same interface* (`SPAN`, `step()`, `paint()`, `glance()`) but
draws a 2D portrait asset instead. `HudCanvas` cannot tell the difference, and
every caller (ui.py, main.py's viseme/audio feeds) works unchanged.

The asset is local only: assets/gojo_avatar.png (supplied, never downloaded).
Its background is a baked-in light checkerboard, so the alpha channel is
rebuilt here at load by flood-filling the background from the image borders —
only *connected* background is keyed, so bright hair inside the silhouette is
never touched. If the asset is missing or unreadable the constructor raises
and the HUD falls back to the mesh avatar.

Animation is deliberately modest: breathing sway, a speaking bounce driven by
the same audio level as the waveform, state drift (thinking/sleeping), and a
hologram scan sweep in the theme colour. No mesh lip-sync — a stable portrait
beats a broken puppet.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QRadialGradient

import PIL.Image
from PIL.Image import LANCZOS
from PyQt6.QtGui import QImage

_ASSET = Path(__file__).resolve().parent.parent / "assets" / "uipic.png"

# Target working height: cheap to scale per frame, sharp enough for the HUD.
_WORK_H = 640

# Full-body assets would render a tiny head in the HUD band, so the portrait
# is framed on the upper body (head + torso + gesture). Fraction of the
# keyed subject height to keep, measured from the top.
_BODY_FRAC = 0.52
# Where the face sits inside that frame, as a fraction from its top. The
# caller centres the head on `cy`, so this puts the face on the old centre.
_FACE_FRAC = 0.20


def _c(col: QColor, a: float) -> QColor:
    q = QColor(col)
    q.setAlpha(int(max(0.0, min(255.0, a))))
    return q


def _finalize(rgba: np.ndarray) -> QPixmap:
    """Crop to the silhouette, frame the upper body, and build the pixmap."""
    h, w, _ = rgba.shape
    alpha = rgba[:, :, 3].astype(np.float32) / 255.0
    ys, xs = np.nonzero(alpha > 0.02)
    if ys.size == 0:
        raise ValueError("empty alpha channel")
    pad = 6
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad)
    y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + pad)
    rgba = rgba[y0:y1, x0:x1]
    # Upper-body framing so the face keeps its presence in the HUD band.
    keep = max(8, int(rgba.shape[0] * _BODY_FRAC))
    rgba = rgba[:keep]
    pic = PIL.Image.fromarray(rgba.astype(np.uint8), "RGBA")
    sc = _WORK_H / pic.height
    pic = pic.resize((max(1, int(pic.width * sc)), _WORK_H), LANCZOS)
    data = pic.tobytes("raw", "RGBA")
    qimg = QImage(data, pic.width, pic.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


def _load_keyed_pixmap() -> QPixmap:
    """Load the portrait with transparency.

    RGBA assets with real transparency are used as-is. RGB assets with a
    baked-in light backdrop fall back to flood-fill keying from the image
    borders — only *connected* background is keyed, so bright hair inside the
    silhouette is never touched.

    Raises on any problem — the caller falls back to the mesh avatar.
    """
    img = PIL.Image.open(_ASSET)
    if img.mode == "RGBA":
        a = np.asarray(img)
        alpha = a[:, :, 3].astype(np.float32) / 255.0
        tfrac = float((alpha < 0.02).mean())
        if 0.05 < tfrac < 0.95 and alpha.mean() > 0.03:
            return _finalize(a)

    rgb = np.asarray(img.convert("RGB")).astype(np.int32)
    h, w, _ = rgb.shape
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    # Near-white, near-grey pixels: a light checkerboard backdrop. Hair has
    # blue shadows and darker rim strokes, so saturation + brightness gates
    # it out.
    is_bg = (lum > 200) & (sat < 28)

    # Flood fill from the borders through bg pixels only (8-neighbourhood via
    # shifts). Anything not connected to the edge survives, whatever its
    # colour — bright hair inside the silhouette is unreachable by design.
    seed = np.zeros_like(is_bg, dtype=bool)
    seed[0, :] = is_bg[0, :]
    seed[-1, :] = is_bg[-1, :]
    seed[:, 0] = is_bg[:, 0]
    seed[:, -1] = is_bg[:, -1]
    filled = seed
    for _ in range(w + h):  # worst-case path, exits early in practice
        up = np.zeros_like(filled); up[1:, :] = filled[:-1, :]
        dn = np.zeros_like(filled); dn[:-1, :] = filled[1:, :]
        lf = np.zeros_like(filled); lf[:, 1:] = filled[:, :-1]
        rt = np.zeros_like(filled); rt[:, :-1] = filled[:, 1:]
        nxt = filled | ((up | dn | lf | rt) & is_bg)
        if nxt.sum() == filled.sum():
            filled = nxt
            break
        filled = nxt

    alpha = (~filled).astype(np.float32)
    if alpha.mean() < 0.05 or alpha.mean() > 0.95:
        raise ValueError("keying produced an implausible silhouette")
    # 3x3 soften so the cut edge is not crunchy.
    nb = alpha.copy()
    nb[1:, :] += alpha[:-1, :]
    nb[:-1, :] += alpha[1:, :]
    nb[:, 1:] += alpha[:, :-1]
    nb[:, :-1] += alpha[:, 1:]
    alpha = np.clip(nb / 5.0, 0.0, 1.0)

    rgba = np.dstack([rgb, (alpha * 255).astype(np.uint8)])
    return _finalize(rgba)


class GojoAvatar:
    """Portrait avatar with the HoloAvatar interface.

    Lifecycle (identical to HoloAvatar):
        av = GojoAvatar()
        av.step(dt, amp, speaking=..., muted=..., state=...)  # once per tick
        av.paint(painter, cx, cy, r, primary, accent, bg)     # once per frame
    """

    # Framing contract with HudCanvas: r is the head half-height in px and the
    # caller fits r to the room via this span. Matches the mesh proportions so
    # the portrait occupies the same band.
    SPAN = 2.2

    def __init__(self) -> None:
        self._base = _load_keyed_pixmap()
        self._scaled: QPixmap | None = None
        self._scaled_h = 0
        self._t = 0.0
        self._sway = 0.0
        self._glow = 0.0        # smoothed audio energy (speaking bounce)
        self._scan = -1.6
        self._drift = [0.0, 0.0]
        self._glance = None     # (dx, dy, until_t)
        self._mute_flag = False

    # ── animation ──────────────────────────────────────────────────────────

    def step(self, dt: float, amp: float, speaking: bool = False,
             muted: bool = False, state: str = "",
             v_open: float | None = None, v_wide: float = 0.0,
             v_level: float | None = None,
             v_seq: list | None = None, v_hop: float = 0.02) -> None:
        dt = max(0.001, min(0.10, float(dt)))
        self._t += dt
        amp = max(0.0, min(1.0, float(amp)))
        self._mute_flag = bool(muted)
        live = speaking and not muted

        # Integrated breathing phase (same no-teleport rule as the mesh).
        speed = (1.0 if not muted else 0.55) * (1.25 if live else 1.0)
        self._sway += dt * speed

        # Speaking bounce follows the voice's own energy, not the peak-held
        # waveform level: prefer the viseme schedule's true levels.
        if v_seq:
            try:
                target = max(float(f[0]) for f in v_seq) if live else 0.0
            except Exception:
                target = amp if live else 0.0
        elif v_level is not None:
            target = float(v_level) if live else 0.0
        else:
            target = amp if live else 0.0
        k = 1.0 - math.exp(-dt / (0.05 if target > self._glow else 0.22))
        self._glow += (max(0.0, min(1.0, target)) - self._glow) * k

        st = (state or "").upper()
        if st in ("THINKING", "PROCESSING"):
            dx_t = 10.0 * math.sin(self._t * 0.45)
            dy_t = 4.0
        elif st in ("SLEEPING", "STANDBY", "OFFLINE"):
            dx_t, dy_t = 0.0, 7.0
        else:
            dx_t, dy_t = 0.0, 0.0
        if self._glance is not None:
            gx, gy, until = self._glance
            if self._t < until:
                dx_t, dy_t = gx * 14.0, gy * 10.0
            else:
                self._glance = None
        ease = 1.0 - math.exp(-dt / 0.6)
        self._drift[0] += (dx_t - self._drift[0]) * ease
        self._drift[1] += (dy_t - self._drift[1]) * ease

        self._scan += dt * (0.55 + 1.5 * self._glow)
        if self._scan > 1.35:
            self._scan = -1.75

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        self._glance = (max(-1.0, min(1.0, float(dx))),
                        max(-1.0, min(1.0, float(dy))),
                        self._t + max(0.1, float(hold)))

    # ── rendering ──────────────────────────────────────────────────────────

    def _pixmap(self, h_px: int) -> QPixmap:
        hq = max(8, int(h_px / 4) * 4)  # quantised cache: no rescale per frame
        if self._scaled is None or self._scaled_h != hq:
            self._scaled = self._base.scaledToHeight(
                hq, Qt.TransformationMode.SmoothTransformation)
            self._scaled_h = hq
        return self._scaled

    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              primary: QColor, accent: QColor, bg: QColor | None = None) -> None:
        if bg is None:
            bg = QColor(0, 0, 0)
        amp = self._glow
        st_drift = self._drift

        # ── aura (same recipe as the mesh: the state colour language stays) ─
        ar = r * 1.95
        grad = QRadialGradient(cx, cy, ar)
        grad.setColorAt(0.00, _c(primary, 34 + 66 * amp))
        grad.setColorAt(0.38, _c(primary, 20 + 40 * amp))
        grad.setColorAt(1.00, _c(primary, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - ar, cy - ar, ar * 2, ar * 2))

        # ── portrait ──────────────────────────────────────────────────────
        # Full subject height fills the band like the mesh head+neck; the face
        # (upper-middle of the portrait) lands on the head centre.
        H = self.SPAN * r * 0.98
        bob = 3.0 * math.sin(self._sway * 0.9)
        scale = (1.0 + 0.008 * math.sin(self._sway * 0.9 + 1.0)
                 + 0.028 * amp)
        pm = self._pixmap(int(H * scale))
        pw, ph = pm.width(), pm.height()
        top = cy - ph * _FACE_FRAC + bob + st_drift[1]
        left = cx - pw / 2 + st_drift[0] + 2.0 * math.sin(self._sway * 0.31)

        p.setOpacity(0.6 if self._mute_flag else 1.0)
        p.drawPixmap(int(left), int(top), pm)
        p.setOpacity(1.0)

        # ── hologram scan sweep in the theme colour ───────────────────────
        sweep = (self._scan + 1.75) / 3.1          # 0..1 down the portrait
        sy = top + sweep * ph
        p.setPen(QPen(_c(accent, 34 + 30 * amp), 1.2))
        p.drawLine(int(left + pw * 0.12), int(sy),
                   int(left + pw * 0.88), int(sy))
