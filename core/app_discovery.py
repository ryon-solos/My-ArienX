"""
core/app_discovery.py — Linux application discovery and generic mapping.

Maps generic application requests (e.g., "text editor") to actual installed binaries.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set


# Generic to specific application mappings for Linux
# Ordered by preference (first match wins)
GENERIC_APP_MAP: Dict[str, List[str]] = {
    "text_editor": [
        "xed", "gedit", "kate", "mousepad", "pluma", "featherpad",
        "code", "codium", "vim", "nvim", "nano", "micro", "neovim"
    ],
    "code_editor": [
        "code", "codium", "vim", "nvim", "neovim", "kate", "gedit", "mousepad"
    ],
    "browser": [
        "google-chrome", "google-chrome-stable", "chrome",
        "firefox", "firefox-esr",
        "brave-browser", "brave",
        "chromium", "chromium-browser",
        "opera", "opera-stable",
        "vivaldi", "vivaldi-stable",
        "msedge", "microsoft-edge",
    ],
    "terminal": [
        "gnome-terminal", "konsole", "xfce4-terminal", "xterm",
        "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
        "x-terminal-emulator", "kgx", "terminator"
    ],
    "file_manager": [
        "nautilus", "nemo", "thunar", "dolphin", "pcmanfm",
        "caja", "pcmanfm-qt", "ranger", "lf", "nnn"
    ],
    "image_viewer": [
        "eog", "gwenview", "feh", "viewnior", "nomacs", "ristretto", "mirage"
    ],
    "pdf_viewer": [
        "evince", "okular", "zathura", "xreader", "atril", "mupdf", "qpdfview"
    ],
    "media_player": [
        "vlc", "mpv", "celluloid", "totem", "smplayer", "kodi"
    ],
    "image_editor": [
        "gimp", "krita", "pinta", "mypaint", "inkscape"
    ],
    "pdf_editor": [
        "okular", "evince", "masterpdfeditor", "pdfstudio", "foxitreader"
    ],
    "code_editor": [
        "code", "codium", "vim", "nvim", "neovim", "kate", "gedit",
        "sublime-text", "subl", "pycharm", "idea"
    ],
    "email_client": [
        "thunderbird", "evolution", "kmail", "geary", "mutt", "neomutt"
    ],
    "calendar": [
        "gnome-calendar", "korganizer", "evolution", "california"
    ],
    "music_player": [
        "spotify", "rhythmbox", "clementine", "strawberry", "audacious", "lollypop"
    ],
    "video_editor": [
        "kdenlive", "openshot", "shotcut", "flowblade", "pitivi"
    ],
    "audio_editor": [
        "audacity", "ardour", "lmms", "reaper"
    ],
    "password_manager": [
        "keepassxc", "keepassx", "bitwarden", "1password"
    ],
    "system_monitor": [
        "gnome-system-monitor", "ksysguard", "htop", "btop", "htop", "glances"
    ],
    "archive_manager": [
        "file-roller", "ark", "engage", "engrampa", "xarchiver"
    ],
    "disk_analyzer": [
        "baobab", "kdirstat", "ncdu", "duc"
    ],
    "backup_tool": [
        "timeshift", "deja-dup", "grsync", "rsync"
    ],
    "network_tool": [
        "nmap", "wireshark", "net-tools", "iproute2", "ssh"
    ],
    "virtualization": [
        "virt-manager", "gnome-boxes", "virtualbox", "qemu", "kvm"
    ],
    "docker": [
        "docker", "podman", "docker-compose", "podman-compose"
    ],
    "database_tool": [
        "dbeaver", "sqlitebrowser", "datagrip", "pgadmin4", "mysql-workbench"
    ],
}

# Aliases for common names -> generic category
APP_NAME_TO_GENERIC: Dict[str, str] = {
    # Text editors
    "notepad": "text_editor",
    "notepad++": "text_editor",
    "textedit": "text_editor",
    "gedit": "text_editor",
    "kate": "text_editor",
    "kwrite": "text_editor",
    "mousepad": "text_editor",
    "pluma": "text_editor",
    "xed": "text_editor",
    "gedit": "text_editor",
    "vim": "text_editor",
    "vi": "text_editor",
    "nano": "text_editor",
    "code": "code_editor",
    "vscode": "code_editor",
    "visual studio code": "code_editor",
    "vscodium": "code_editor",
    "codium": "code_editor",
    
    # Browsers
    "chrome": "browser",
    "google chrome": "browser",
    "chromium": "browser",
    "firefox": "browser",
    "firefox-esr": "browser",
    "brave": "browser",
    "brave browser": "browser",
    "opera": "browser",
    "opera gx": "browser",
    "vivaldi": "browser",
    "edge": "browser",
    "microsoft edge": "browser",
    "msedge": "browser",
    
    # Terminals
    "terminal": "terminal",
    "cmd": "terminal",
    "command prompt": "terminal",
    "powershell": "terminal",
    "bash": "terminal",
    "zsh": "terminal",
    "fish": "terminal",
    "konsole": "terminal",
    "gnome-terminal": "terminal",
    "xfce4-terminal": "terminal",
    "xterm": "terminal",
    
    # File managers
    "explorer": "file_manager",
    "file explorer": "file_manager",
    "finder": "file_manager",
    "nautilus": "file_manager",
    "nemo": "file_manager",
    "thunar": "file_manager",
    "dolphin": "file_manager",
    
    # Image viewers
    "photos": "image_viewer",
    "image viewer": "image_viewer",
    "preview": "image_viewer",
    
    # PDF
    "pdf": "pdf_viewer",
    "pdf reader": "pdf_viewer",
    "acrobat": "pdf_viewer",
    "adobe reader": "pdf_viewer",
    
    # Media
    "vlc": "media_player",
    "mpv": "media_player",
    "media player": "media_player",
    "music": "music_player",
    "music player": "music_player",
    "spotify": "music_player",
    
    # System
    "task manager": "system_monitor",
    "activity monitor": "system_monitor",
    "system monitor": "system_monitor",
    "htop": "system_monitor",
    "btop": "system_monitor",
    
    # Terminals
    "terminal": "terminal",
    "command prompt": "terminal",
    "cmd": "terminal",
    "powershell": "terminal",
    
    # Settings
    "settings": "settings",
    "preferences": "settings",
    "control panel": "settings",
    "system preferences": "settings",
    
    # Calculator
    "calculator": "calculator",
    "calc": "calculator",
    
    # Screenshot
    "screenshot": "screenshot",
    "snip": "screenshot",
    "snipping tool": "screenshot",
    
    # Terminal emulators
    "gnome-terminal": "terminal",
    "konsole": "terminal",
    "xfce4-terminal": "terminal",
    "xterm": "terminal",
    "rxvt": "terminal",
    "urxvt": "terminal",
    "alacritty": "terminal",
    "kitty": "terminal",
    "wezterm": "terminal",
    "tilix": "terminal",
    "terminator": "terminal",
    "guake": "terminal",
    "yakuake": "terminal",
}


def _get_os() -> str:
    import platform
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(platform.system(), "linux")


def _get_os_linux() -> str:
    import platform
    return platform.system().lower()


def normalize_app_name(raw: str) -> str:
    """Normalize a raw app name to a generic category or specific binary."""
    key = raw.lower().strip()
    
    # Direct generic mapping
    if key in APP_NAME_TO_GENERIC:
        return APP_NAME_TO_GENERIC[key]
    
    # Substring match (e.g., "visual studio code" -> "code_editor")
    for alias, generic in APP_NAME_TO_GENERIC.items():
        if alias in key or key in alias:
            return generic
    
    return raw


def resolve_app(generic_or_name: str) -> Optional[str]:
    """
    Resolve a generic category or app name to an actual binary.
    
    Returns the binary name if found, None otherwise.
    """
    generic = normalize_app_name(generic_or_name)
    
    # If it's already a known binary, verify it exists
    if shutil.which(generic_or_name):
        return generic_or_name
    
    # Try generic mapping
    if generic in GENERIC_APP_MAP:
        for candidate in GENERIC_APP_MAP[generic]:
            if shutil.which(candidate):
                return candidate
    
    # Try direct lookup
    if shutil.which(generic_or_name):
        return generic_or_name
    
    # Fallback: try the raw name
    if shutil.which(generic_or_name):
        return generic_or_name
    
    return None


def find_app(generic_or_name: str) -> Optional[str]:
    """Find an application binary by generic name or specific name."""
    return resolve_app(generic_or_name)


def get_generic_for_app(app_name: str) -> Optional[str]:
    """Get the generic category for a known app name."""
    return APP_NAME_TO_GENERIC.get(app_name.lower())


def list_generics() -> List[str]:
    """List all known generic categories."""
    return sorted(GENERIC_APP_MAP.keys())


def get_available_in_category(generic: str) -> List[str]:
    """Return all available binaries for a generic category."""
    if generic not in GENERIC_APP_MAP:
        return []
    return [c for c in GENERIC_APP_MAP[generic] if shutil.which(c)]


def discover_installed_apps() -> Dict[str, str]:
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
                    bin_name = exec_line.split()[0]
                    if bin_name and bin_name not in apps.values():
                        apps[Path(f).stem] = bin_name
            except Exception:
                continue
    return apps


def is_app_installed(app_name: str) -> bool:
    """Check if an application is installed (via which)."""
    return shutil.which(app_name) is not None


def get_app_path(app_name: str) -> Optional[str]:
    """Get full path to an application binary."""
    path = shutil.which(app_name)
    return path if path else None