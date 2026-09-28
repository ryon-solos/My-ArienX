"""
core/environment.py — Environment context and discovery.

Lightweight environment awareness: OS, desktop, display, apps, hardware.
Cached with TTL; refreshed on demand.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Optional


@dataclass
class MonitorInfo:
    x: int
    y: int
    width: int
    height: int
    scale: float = 1.0
    is_primary: bool = False
    name: str = ""


@dataclass
class EnvironmentContext:
    """
    Lightweight environment snapshot.
    
    Cached with TTL; refreshed on demand via refresh().
    """
    # OS / Platform
    os: str = ""                    # "Linux", "Darwin", "Windows"
    distro: str = ""                # "Linux Mint 21", "Ubuntu 22.04"
    desktop: str = ""               # "Cinnamon", "GNOME", "KDE"
    display_server: str = ""        # "X11", "Wayland"
    
    # Display
    display_resolution: Tuple[int, int] = (0, 0)
    screen_count: int = 0
    monitors: List[MonitorInfo] = field(default_factory=list)
    
    # Shell / CLI
    shell: str = ""
    
    # Applications
    browsers: List[str] = field(default_factory=list)
    installed_apps: Dict[str, str] = field(default_factory=dict)  # generic -> binary
    
    # Hardware
    audio_available: bool = True
    screen_available: bool = True
    camera_available: bool = True
    gpu_available: bool = False
    
    # Audio devices (from audio_devices module)
    input_devices: List[str] = field(default_factory=list)
    output_devices: List[str] = field(default_factory=list)
    
    # Caching
    _cache_ttl: float = 300.0  # 5 minutes
    _last_refresh: float = 0.0
    _refreshed_once: bool = False
    
    def is_stale(self) -> bool:
        """True if cache has expired or never been populated."""
        return not self._refreshed_once or (time.monotonic() - self._last_refresh) > self._cache_ttl
    
    def mark_refreshed(self) -> None:
        self._last_refresh = time.monotonic()
        self._refreshed_once = True


# ── Discovery helpers ────────────────────────────────────────────────

def _run(cmd: List[str], timeout: float = 5.0) -> str:
    """Run command, return stdout or empty string on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except Exception:
        return ""


def _detect_os() -> tuple[str, str]:
    """Return (os, distro)."""
    system = platform.system()
    if system == "Linux":
        distro = ""
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        distro = line.split("=", 1)[1].strip().strip('"')
                        break
        except Exception:
            pass
        return "Linux", distro
    elif system == "Darwin":
        return "Darwin", _run(["sw_vers", "-productVersion"])
    elif system == "Windows":
        return "Windows", platform.version()
    return system, ""


def _detect_desktop() -> str:
    """Detect desktop environment."""
    for var in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "GDMSESSION"):
        val = os.environ.get(var, "")
        if val:
            return val.lower()
    return ""


def _detect_display_server() -> str:
    """Detect display server."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "Wayland"
    if os.environ.get("DISPLAY"):
        return "X11"
    return ""


def _get_display_info() -> Tuple[int, int, List[MonitorInfo]]:
    """Get display resolution and monitor info via xrandr/mss."""
    monitors = []
    total_w = total_h = 0
    
    # Try xrandr first
    out = _run(["xrandr", "--current"])
    if out:
        current_mon = ""
        for line in out.splitlines():
            line = line.strip()
            if " connected" in line:
                parts = line.split()
                current_mon = parts[0]
                # Parse resolution
                for part in parts:
                    if "x" in part and "+" in part:
                        # e.g., 1920x1080+0+0
                        res_part = part.split("+")[0]
                        if "x" in res_part:
                            w, h = res_part.split("x")
                            x_off, y_off = part.split("+")[1:3]
                            monitors.append(MonitorInfo(
                                x=int(x_off), y=int(y_off),
                                width=int(w), height=int(h),
                                name=current_mon
                            ))
                            total_w = max(total_w, int(x_off) + int(w))
                            total_h = max(total_h, int(y_off) + int(h))
        if monitors:
            return total_w or max(m.width for m in monitors), total_h or max(m.height for m in monitors), monitors
    
    # Fallback: mss
    try:
        import mss
        with mss.mss() as sct:
            mons = sct.monitors
            if len(mons) > 1:
                primary = mons[1]
                return primary["width"], primary["height"], [
                    MonitorInfo(x=m["left"], y=m["top"], width=m["width"], height=m["height"],
                               name=f"monitor_{i}", is_primary=(i==1))
                    for i, m in enumerate(mons[1:], 1)
                ]
    except Exception:
        pass
    
    return 1920, 1080, []


def _detect_shell() -> str:
    shell = os.environ.get("SHELL", "")
    if shell:
        return Path(shell).name
    return "bash"


def _discover_browsers() -> List[str]:
    """Return list of available browser binaries."""
    candidates = [
        "google-chrome", "google-chrome-stable", "chrome",
        "firefox", "firefox-esr",
        "brave-browser", "brave",
        "chromium", "chromium-browser",
        "opera", "opera-stable",
        "vivaldi", "vivaldi-stable",
        "msedge", "microsoft-edge",
    ]
    found = []
    for cmd in candidates:
        if shutil.which(cmd):
            found.append(cmd)
    return found


def _discover_installed_apps() -> Dict[str, str]:
    """
    Discover installed applications via .desktop files.
    Returns mapping: generic_name -> binary_name
    """
    apps = {}
    desktop_dirs = [
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path.home() / ".local/share/applications",
    ]
    
    for d in desktop_dirs:
        if not d.exists():
            continue
        for f in d.glob("*.desktop"):
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                if "NoDisplay=true" in content:
                    continue
                exec_line = ""
                name = ""
                for line in content.splitlines():
                    if line.startswith("Exec="):
                        exec_line = line[5:].split()[0]
                    elif line.startswith("Name="):
                        name = line[5:]
                if exec_line:
                    bin_name = Path(exec_line).name
                    if bin_name and bin_name not in apps.values():
                        apps[Path(f).stem] = bin_name
            except Exception:
                continue
    return apps


def _get_audio_devices() -> tuple[List[str], List[str]]:
    """Get available input/output device names."""
    try:
        from core.audio_devices import get_input_devices, get_output_devices
        return get_input_devices(), get_output_devices()
    except Exception:
        return [], []


def _check_camera() -> bool:
    """Quick check if camera is available."""
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        ok = cap.isOpened()
        cap.release()
        return ok
    except Exception:
        return False


def _check_gpu() -> bool:
    """Check if GPU acceleration is available."""
    for cmd in [["nvidia-smi"], ["rocm-smi"], ["intel_gpu_top"]]:
        if _run(cmd):
            return True
    return False


def _get_shell() -> str:
    shell = os.environ.get("SHELL", "")
    if shell:
        return Path(shell).name
    return "bash"


# ── Main discovery function ────────────────────────────────────────

def discover_environment() -> EnvironmentContext:
    """Perform full environment discovery."""
    ctx = EnvironmentContext()
    
    # OS / Platform
    ctx.os, ctx.distro = _detect_os()
    ctx.desktop = _detect_desktop()
    ctx.display_server = _detect_display_server()
    
    # Display
    w, h, monitors = _get_display_info()
    ctx.display_resolution = (w, h)
    ctx.monitors = monitors
    ctx.screen_count = len(monitors) if monitors else 1
    
    # Shell
    ctx.shell = _get_shell()
    
    # Apps
    ctx.browsers = _discover_browsers()
    ctx.installed_apps = _discover_installed_apps()
    
    # Hardware
    ctx.audio_available = True  # assume unless proven otherwise
    ctx.screen_available = True
    ctx.camera_available = _check_camera()
    ctx.gpu_available = _check_gpu()
    
    # Audio devices
    ctx.input_devices, ctx.output_devices = _get_audio_devices()
    
    ctx.mark_refreshed()
    return ctx


def get_environment() -> EnvironmentContext:
    """Get cached environment context, refreshing if stale."""
    global _cached_env
    if _cached_env is None or _cached_env.is_stale():
        _cached_env = discover_environment()
    return _cached_env


# Module-level cache
_cached_env: Optional[EnvironmentContext] = None