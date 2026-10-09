"""Mobile projections over the existing bridge; never export worker results or files."""
from __future__ import annotations
import json
import threading
from pathlib import Path
import psutil

_lock = threading.Lock()
_workers: list[dict] = []


def record_workers(snapshot: dict) -> None:
    global _workers
    rows = []
    for worker in snapshot.get("workers", [])[:30]:
        # Headings and diagnostics may contain private prompts: publish only role metadata.
        rows.append({"id": str(worker.get("id", ""))[:80], "heading": "Worker",
                     "provider": str(worker.get("provider", ""))[:80],
                     "model": str(worker.get("model", ""))[:80],
                     "status": str(worker.get("status", ""))[:40],
                     "progress": worker.get("progress"), "runtime": worker.get("elapsed_seconds")})
    with _lock:
        _workers = rows


def snapshot() -> dict:
    from extensions.runtime import get_runtime
    battery = psutil.sensors_battery()
    with _lock:
        workers = list(_workers)
    apps = []
    for app in get_runtime().list():
        try:
            manifest = json.loads((app.root / "manifest.json").read_text())
        except (OSError, ValueError):
            manifest = {}
        apps.append({"id": app.id, "name": app.name, "version": app.version,
                     "enabled": app.enabled, "status": app.status,
                     "permissions": manifest.get("permissions", [])})
    return {"cpu": psutil.cpu_percent(), "ram": psutil.virtual_memory().percent,
            "battery": battery.percent if battery else None, "workers": workers,
            "extensions": apps}


def pairing_payload(value: dict) -> str:
    """Only valid opaque one-use codes are rendered, never a private device key."""
    import re
    from urllib.parse import urlparse, parse_qs
    raw = str(value.get("qr", ""))
    parsed = urlparse(raw)
    fields = parse_qs(parsed.query)
    cloud = urlparse(fields.get("cloud", [""])[0])
    if parsed.scheme != "arienx" or parsed.netloc != "pair" or cloud.scheme != "https" or not cloud.hostname or cloud.username or not re.fullmatch(r"[a-f0-9]{64}", fields.get("code", [""])[0]):
        raise ValueError("Cloud returned an invalid pairing code")
    return raw
