"""Bounded screen/camera awareness state."""

from __future__ import annotations

import threading
import time


class VisionState:
    def __init__(self, screen_available: bool = True, camera_available: bool = True,
                 freshness_seconds: float = 45.0):
        self._lock = threading.Lock()
        self.screen_available = bool(screen_available)
        self.camera_available = bool(camera_available)
        self.screen_active = False
        self.camera_active = False
        self.last_screen_observation = ""
        self.last_camera_observation = ""
        self.last_screen_at = 0.0
        self.last_camera_at = 0.0
        self.freshness_seconds = freshness_seconds

    def set_available(self, source: str, available: bool) -> None:
        with self._lock:
            setattr(self, f"{source}_available", bool(available))

    def set_active(self, source: str, active: bool) -> None:
        with self._lock:
            setattr(self, f"{source}_active", bool(active))

    def observe(self, source: str, summary: str = "") -> None:
        now = time.monotonic()
        with self._lock:
            setattr(self, f"last_{source}_observation", str(summary or "captured")[:160])
            setattr(self, f"last_{source}_at", now)
            setattr(self, f"{source}_active", False)

    def is_fresh(self, source: str) -> bool:
        with self._lock:
            return bool(getattr(self, f"last_{source}_at", 0.0)) and (
                time.monotonic() - getattr(self, f"last_{source}_at")
                <= self.freshness_seconds
            )

    def snapshot(self) -> dict:
        with self._lock:
            now = time.monotonic()
            return {
                "screen_available": self.screen_available,
                "screen_active": self.screen_active,
                "camera_available": self.camera_available,
                "camera_active": self.camera_active,
                "screen_fresh": bool(self.last_screen_at) and now - self.last_screen_at <= self.freshness_seconds,
                "camera_fresh": bool(self.last_camera_at) and now - self.last_camera_at <= self.freshness_seconds,
                "last_screen_observation": self.last_screen_observation,
                "last_camera_observation": self.last_camera_observation,
            }
