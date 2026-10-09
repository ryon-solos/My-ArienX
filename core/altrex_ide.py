"""Launch the bundled ALTREX CODE workspace as its own Electron application."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parent.parent / "ALTEX-CODE-main" / "ALTREX-CODE"


def _pnpm() -> str:
    """Find pnpm when ArienX was launched from a desktop entry without PATH."""
    candidates = (
        shutil.which("pnpm"),
        str(Path.home() / ".local" / "share" / "pnpm" / "bin" / "pnpm"),
        str(Path.home() / ".local" / "bin" / "pnpm"),
    )
    return next((path for path in candidates if path and Path(path).is_file()), "")


def _node_bin() -> Path | None:
    direct = shutil.which("node")
    if direct:
        return Path(direct).parent
    # NVM is normally loaded by an interactive shell, not a .desktop launcher.
    installed = sorted((Path.home() / ".nvm" / "versions" / "node").glob("*/bin/node"))
    return installed[-1].parent if installed else None


def launch() -> str:
    root = _root()
    if not (root / "package.json").is_file():
        return "ALTREX CODE source was not found in ALTEX-CODE-main/ALTREX-CODE."
    if not (root / "node_modules").is_dir():
        return "ALTREX CODE needs its dependencies installed first: install Node 22+ and pnpm, then run 'pnpm install' in ALTEX-CODE-main/ALTREX-CODE."
    pnpm = _pnpm()
    if not pnpm:
        return "ALTREX CODE could not find pnpm. Install Node 22+ with pnpm, then reopen ArienX."
    node_bin = _node_bin()
    if node_bin is None:
        return "ALTREX CODE could not find Node. Install Node 22+ and reopen ArienX."
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join((str(node_bin), str(Path(pnpm).parent), env.get("PATH", "")))
        log_path = Path("/tmp/arienx-altrex-ide.log")
        with log_path.open("ab") as log:
            subprocess.Popen([pnpm, "dev"], cwd=root, stdout=log,
                             stderr=subprocess.STDOUT, creationflags=flags,
                             start_new_session=True, env=env)
        return "ALTREX CODE is starting in its own window. Startup log: /tmp/arienx-altrex-ide.log"
    except Exception as exc:
        return f"Could not start ALTREX CODE ({type(exc).__name__})."
