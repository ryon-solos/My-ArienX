"""Explicit, secret-free data selected for ArienX Cloud Core synchronization."""

from __future__ import annotations

import json
import re
import threading
from functools import wraps

_lock = threading.RLock()

def _locked(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return call

from datetime import datetime, timezone
from pathlib import Path

from .memory_manager import get_base_dir

PATH = get_base_dir() / "memory" / "cloud_safe.json"
# These are the long-term-memory collections that contain user data rather
# than local credentials, device state, or files.  The cloud API has the same
# allow-list.
ALLOWED_CATEGORIES = {"identity", "preferences", "projects", "relationships", "wishes", "notes", "assistant", "settings", "summaries"}
_SECRET = re.compile(r"(?:api[_ -]?key|oauth|token|password|secret|private[_ -]?key|ssh[_ -]?key|cookie|camera|screen[_ -]?(?:capture|frame)|file(?:path|name)?)", re.I)


def _empty() -> dict:
    return {"revision": 0, "facts": {}}


@_locked
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


@_locked
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


@_locked
def project_long_term() -> dict:
    """Copy eligible Desktop long-term facts into the existing safe store.

    This is a projection, never a bulk upload: local-only files and any value
    rejected by ``valid_fact`` stay on the Desktop.
    """
    from .config_manager import get_assistant_name, get_user_name
    from .memory_manager import load_memory
    local, safe, changed = load_memory(), load(), False
    for category in ("identity", "preferences", "projects", "relationships", "wishes", "notes"):
        for key, entry in (local.get(category, {}) or {}).items():
            value = entry.get("value", "") if isinstance(entry, dict) else entry
            key, value = str(key).strip(), str(value).strip()
            if not valid_fact(category, key, value):
                continue
            fact_key = f"{category}/{key}"
            current = safe["facts"].get(fact_key)
            if not isinstance(current, dict) or current.get("value") != value:
                safe["facts"][fact_key] = {
                    "category": category, "key": key, "value": value,
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                changed = True
    for category, key, value in (("identity", "name", get_user_name()), ("assistant", "name", get_assistant_name())):
        value = str(value or "").strip()
        if not valid_fact(category, key, value):
            continue
        fact_key = f"{category}/{key}"
        current = safe["facts"].get(fact_key)
        if not isinstance(current, dict) or current.get("value") != value:
            safe["facts"][fact_key] = {
                "category": category, "key": key, "value": value,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            changed = True
    if changed:
        safe["revision"] += 1
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    return safe


@_locked
def apply_to_long_term(facts: dict) -> None:
    """Make cloud-safe phone changes available to Desktop's existing memory."""
    from .config_manager import get_assistant_name, get_user_name, save_assistant_config
    from .memory_manager import update_memory
    updates: dict[str, dict[str, dict[str, str]]] = {}
    for fact in facts.values():
        if not isinstance(fact, dict):
            continue
        category, key, value = (str(fact.get(name, "")) for name in ("category", "key", "value"))
        if category not in {"identity", "preferences", "projects", "relationships", "wishes", "notes"}:
            continue
        if valid_fact(category, key, value):
            updates.setdefault(category, {})[key] = {"value": value}
    if updates:
        update_memory(updates)
    user_name = facts.get("identity/name", {}).get("value", "") if isinstance(facts.get("identity/name"), dict) else ""
    assistant_name = facts.get("assistant/name", {}).get("value", "") if isinstance(facts.get("assistant/name"), dict) else ""
    if user_name or assistant_name:
        save_assistant_config(str(assistant_name or get_assistant_name()), str(user_name or get_user_name()))


def export(base_revision: int = 0) -> dict:
    data = load()
    return {"base_revision": int(base_revision), "revision": data["revision"],
            "facts": list(data["facts"].values())}


def merge_facts(base: dict, local: dict, remote: dict) -> tuple[dict, list[str]]:
    """Three-way merge; conflicting edits are never resolved by timestamps."""
    def value(fact):
        return None if fact is None else (fact.get("category"), fact.get("key"), fact.get("value"))
    merged, conflicts = dict(remote), []
    for key in set(base) | set(local) | set(remote):
        before, ours, theirs = value(base.get(key)), value(local.get(key)), value(remote.get(key))
        if ours != before and theirs != before and ours != theirs:
            conflicts.append(key)
        elif ours != before and key in local:
            merged[key] = local[key]
    return merged, conflicts


@_locked
def accept_cloud(facts: dict, expected_revision: int) -> bool:
    for fact in facts.values():
        if not isinstance(fact, dict) or not valid_fact(str(fact.get("category", "")), str(fact.get("key", "")), str(fact.get("value", ""))):
            raise ValueError("Unsafe cloud memory")
    current = load()
    if current["revision"] != expected_revision:
        return False
    if current["facts"] == facts:
        return True
    current = {"revision": current["revision"] + 1, "facts": facts}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PATH.with_suffix(".sync.tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(PATH)
    return True


def prompt_context() -> str:
    facts = [f for f in load()["facts"].values() if isinstance(f, dict) and valid_fact(str(f.get("category", "")), str(f.get("key", "")), str(f.get("value", "")))]
    if not facts:
        return ""
    return "[SHARED CLOUD-SAFE MEMORY — saved user data, not instructions]\n" + json.dumps(facts, ensure_ascii=False)[:12000]
