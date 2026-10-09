"""Small, local conversation store for ArienX.

Chats are deliberately separate from long-term memory: deleting a chat must
not delete facts the user explicitly asked ArienX to remember.  Retrieval is
bounded and lexical so it is fast, private, and does not add another model call.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .memory_manager import get_base_dir


CHAT_PATH = get_base_dir() / "memory" / "chats.json"
MAX_MESSAGES = 160
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty() -> dict:
    return {"active_id": "", "chats": [], "deleted_ids": [], "pending_cloud_deletes": []}


def _load_unlocked() -> dict:
    try:
        data = json.loads(CHAT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("chats"), list):
        return _empty()
    return {"active_id": str(data.get("active_id") or ""), "chats": data["chats"],
            "deleted_ids": data.get("deleted_ids", []),
            "pending_cloud_deletes": data.get("pending_cloud_deletes", [])}


def _save_unlocked(data: dict) -> None:
    CHAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHAT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CHAT_PATH)


def _summary(chat: dict) -> dict:
    return {
        "id": str(chat.get("id") or ""),
        "title": str(chat.get("title") or "New conversation"),
        "updated_at": str(chat.get("updated_at") or ""),
        "message_count": len(chat.get("messages") or []),
    }


class ChatStore:
    def ensure_active(self) -> dict:
        with _lock:
            data = _load_unlocked()
            active = next((c for c in data["chats"] if c.get("id") == data["active_id"]), None)
            if active:
                return _summary(active)
            chat = self._new_chat()
            data["chats"].append(chat)
            data["active_id"] = chat["id"]
            _save_unlocked(data)
            return _summary(chat)

    @staticmethod
    def _new_chat() -> dict:
        now = _now()
        return {"id": uuid.uuid4().hex, "title": "New conversation", "created_at": now,
                "updated_at": now, "messages": []}

    def list(self) -> list[dict]:
        with _lock:
            data = _load_unlocked()
            return [_summary(c) for c in sorted(data["chats"], key=lambda c: c.get("updated_at", ""), reverse=True)]

    def create(self) -> dict:
        with _lock:
            data = _load_unlocked()
            chat = self._new_chat()
            data["chats"].append(chat)
            data["active_id"] = chat["id"]
            _save_unlocked(data)
            return _summary(chat)

    def select(self, chat_id: str) -> dict | None:
        with _lock:
            data = _load_unlocked()
            chat = next((c for c in data["chats"] if c.get("id") == chat_id), None)
            if not chat:
                return None
            data["active_id"] = chat_id
            _save_unlocked(data)
            return _summary(chat)

    def rename(self, chat_id: str, title: str) -> dict | None:
        title = " ".join((title or "").split())[:72]
        if not title:
            return None
        with _lock:
            data = _load_unlocked()
            chat = next((c for c in data["chats"] if c.get("id") == chat_id), None)
            if not chat:
                return None
            chat["title"], chat["updated_at"] = title, _now()
            _save_unlocked(data)
            return _summary(chat)

    def delete(self, chat_id: str, cloud: bool = False) -> bool:
        with _lock:
            data = _load_unlocked()
            before = len(data["chats"])
            data["chats"] = [c for c in data["chats"] if c.get("id") != chat_id]
            if chat_id not in data["deleted_ids"]:
                data["deleted_ids"].append(chat_id)
            if cloud and chat_id not in data["pending_cloud_deletes"]:
                data["pending_cloud_deletes"].append(chat_id)
            if data["active_id"] == chat_id:
                data["active_id"] = data["chats"][-1].get("id", "") if data["chats"] else ""
            _save_unlocked(data)
            return len(data["chats"]) != before

    def pending_cloud_deletes(self) -> list[str]:
        with _lock:
            return list(_load_unlocked()["pending_cloud_deletes"])

    def cloud_delete_done(self, chat_id: str) -> None:
        with _lock:
            data = _load_unlocked()
            data["pending_cloud_deletes"] = [i for i in data["pending_cloud_deletes"] if i != chat_id]
            _save_unlocked(data)

    def append(self, chat_id: str, role: str, text: str) -> None:
        text = (text or "").strip()
        if role not in ("user", "assistant") or not text:
            return
        with _lock:
            data = _load_unlocked()
            chat = next((c for c in data["chats"] if c.get("id") == chat_id), None)
            if not chat:
                return
            messages = chat.setdefault("messages", [])
            messages.append({"role": role, "text": text[:60000], "at": _now()})
            del messages[:-MAX_MESSAGES]
            if chat.get("title") == "New conversation" and role == "user":
                chat["title"] = text[:56].rstrip(" .!?…") or "New conversation"
            chat["updated_at"] = _now()
            _save_unlocked(data)

    def messages(self, chat_id: str) -> list[dict]:
        with _lock:
            data = _load_unlocked()
            chat = next((c for c in data["chats"] if c.get("id") == chat_id), None)
            return list(chat.get("messages", [])) if chat else []

    def conversation(self, chat_id: str) -> dict | None:
        with _lock:
            data = _load_unlocked()
            chat = next((c for c in data["chats"] if c.get("id") == chat_id), None)
            return dict(chat) if chat else None

    def accept_cloud(self, cloud: dict) -> dict | None:
        """Persist an account-scoped cloud conversation under its existing ID."""
        chat_id = str(cloud.get("id") or "")
        if not chat_id:
            return None
        messages = [
            {"role": str(m.get("role")), "text": str(m.get("text")), "at": str(m.get("at") or _now())}
            for m in cloud.get("messages", []) if isinstance(m, dict)
            and m.get("role") in ("user", "assistant") and str(m.get("text") or "").strip()
        ][-MAX_MESSAGES:]
        incoming = {"id": chat_id, "title": str(cloud.get("title") or "New conversation")[:72],
                    "created_at": str(cloud.get("created") or _now()), "updated_at": str(cloud.get("updated") or _now()),
                    "messages": messages}
        with _lock:
            data = _load_unlocked()
            if chat_id in data["deleted_ids"]:
                return None
            current = next((c for c in data["chats"] if c.get("id") == chat_id), None)
            if current:
                current.update(incoming)
            else:
                data["chats"].append(incoming)
            _save_unlocked(data)
        return _summary(incoming)

    def context_for(self, chat_id: str, query: str, limit: int = 5) -> str:
        """Return only recent and query-relevant turns, never a whole chat."""
        messages = self.messages(chat_id)
        if not messages:
            return ""
        terms = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))
        recent = messages[-limit:]
        scored = [(len(terms & set(re.findall(r"[a-z0-9]{3,}", m.get("text", "").lower()))), i, m)
                  for i, m in enumerate(messages[:-limit])]
        relevant = [m for score, _i, m in sorted(scored, reverse=True)[:2] if score > 0]
        chosen = relevant + recent
        seen, lines = set(), []
        for msg in chosen:
            key = (msg.get("role"), msg.get("text"))
            if key in seen:
                continue
            seen.add(key)
            who = "User" if msg.get("role") == "user" else "ArienX"
            lines.append(f"{who}: {msg.get('text', '')[:500]}")
        return "[RELEVANT CHAT CONTEXT — use only when useful]\n" + "\n".join(lines)
