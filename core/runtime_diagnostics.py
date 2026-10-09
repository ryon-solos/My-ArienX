"""Persist fatal native traces and uncaught exceptions without logging chats."""
from __future__ import annotations
import atexit
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading

_fault_file = None
_reporter = None


def event(name, **fields):
    logging.getLogger("ArienX.runtime").info("%s %s", name,
        " ".join(f"{key}={value}" for key, value in fields.items()))


def set_reporter(callback):
    global _reporter
    _reporter = callback


def install():
    global _fault_file
    if _fault_file is not None:
        return
    directory = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "ArienX"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fault_path = directory / "crash.log"
        if fault_path.exists() and fault_path.stat().st_size > 2_000_000:
            fault_path.replace(directory / "crash.previous.log")
        _fault_file = fault_path.open("a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
        logger = logging.getLogger("ArienX.runtime")
        logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(directory / "runtime.log", maxBytes=2_000_000, backupCount=1)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
        logger.info("Started pid=%s", os.getpid())
        atexit.register(lambda: logger.info("Normal exit pid=%s", os.getpid()))

        def report(kind, value, trace):
            logger.error("Uncaught exception", exc_info=(kind, value, trace))
            sys.__excepthook__(kind, value, trace)
            if _reporter:
                try:
                    _reporter("ERR: An unexpected error was recorded in " + str(directory / "runtime.log"))
                except RuntimeError:
                    pass  # the UI may already have closed
        sys.excepthook = report
        threading.excepthook = lambda args: report(args.exc_type, args.exc_value, args.exc_traceback)
    except OSError as exc:
        print(f"[Diagnostics] Cannot create crash log: {exc}", file=sys.stderr)
