"""Explicit, secret-free data selected for ArienX Cloud Core synchronization."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .memory_manager import get_base_dir

PATH = get_base_dir() / "memory" / "cloud_safe.json"
ALLOWED_CATEGORIES = {"preferences", "projects", "notes"}
_SECRET = re.compile(r"(?:api[_ -]?key|token|password|secret|private[_ -]?key)", re.I)


def _empty() -> dict:
    return {"revision": 0, "facts": {}}


def load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("facts"), dict):
        return _empty()
    return {"revision": int(data.get("revision", 0) or 0), "facts": data["facts"]}


def valid_fact(category: str, key: str, value: str) -> bool:
    return (category in ALLOWED_CATEGORIES and bool(key.strip()) and len(key) <= 80
            and bool(value.strip()) and len(value) <= 380
            and not _SECRET.search(key) and not _SECRET.search(value))


def upsert(category: str, key: str, value: str) -> dict:
    if not valid_fact(category, key, value):
        raise ValueError("Cloud-safe memory rejects secrets and unsupported fields")
    data = load()
    data["facts"][f"{category}/{key.strip()}"] = {
        "category": category, "key": key.strip(), "value": value.strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    data["revision"] += 1
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def export(base_revision: int = 0) -> dict:
    data = load()
    return {"base_revision": int(base_revision), "revision": data["revision"],
            "facts": list(data["facts"].values())}
