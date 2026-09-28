"""Optional ArienX device bridge. The private signing key never leaves device."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from memory.config_manager import _patch_config, load_api_keys


@dataclass
class BridgeConfig:
    url: str = ""
    access_token: str = ""
    device_id: str = ""
    private_key_pem: str = ""
    cloud_memory_revision: int = 0
    enabled: bool = False


def get_config() -> BridgeConfig:
    raw = load_api_keys().get("cloud_bridge")
    raw = raw if isinstance(raw, dict) else {}
    return BridgeConfig(**{key: raw.get(key, getattr(BridgeConfig(), key))
                           for key in BridgeConfig.__annotations__})


def provision(url: str, access_token: str) -> BridgeConfig:
    key = Ed25519PrivateKey.generate()
    cfg = BridgeConfig(url=url.rstrip("/"), access_token=access_token.strip(),
                       device_id=uuid.uuid4().hex,
                       private_key_pem=key.private_bytes(
                           serialization.Encoding.PEM,
                           serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode(), enabled=True)
    _patch_config(cloud_bridge=cfg.__dict__)
    return cfg


class CloudBridge:
    """Short HTTPS calls only; Netlify never holds an open device connection."""
    def __init__(self, cfg: BridgeConfig | None = None):
        self.cfg = cfg or get_config()

    @property
    def configured(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.url and self.cfg.device_id and self.cfg.private_key_pem)

    def _signed_headers(self, method: str, path: str, body: str = "") -> dict:
        key = serialization.load_pem_private_key(self.cfg.private_key_pem.encode(), password=None)
        stamp = str(int(time.time()))
        digest = hashlib.sha256(body.encode()).hexdigest()
        message = f"{stamp}\n{method}\n{path}\n{digest}".encode()
        signature = base64.b64encode(key.sign(message)).decode()
        return {"X-ArienX-Device": self.cfg.device_id, "X-ArienX-Time": stamp,
                "X-ArienX-Signature": signature, "Content-Type": "application/json"}

    def register(self, label: str = "ArienX device") -> bool:
        if not self.configured or not self.cfg.access_token:
            return False
        key = serialization.load_pem_private_key(self.cfg.private_key_pem.encode(), password=None)
        public_key = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        body = json.dumps({"device_id": self.cfg.device_id, "label": label[:80], "public_key": public_key})
        response = requests.post(self.cfg.url + "/api/bridge/register", data=body,
            headers={"Authorization": f"Bearer {self.cfg.access_token}", "Content-Type": "application/json"}, timeout=10)
        return response.ok

    def heartbeat(self, capabilities: list[str]) -> bool:
        if not self.configured:
            return False
        path, body = "/api/bridge/heartbeat", json.dumps({"capabilities": capabilities})
        response = requests.post(self.cfg.url + path, data=body,
                                 headers=self._signed_headers("POST", path, body), timeout=8)
        return response.ok

    def poll_tasks(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        path = "/api/bridge/tasks"
        response = requests.get(self.cfg.url + path, headers=self._signed_headers("GET", path), timeout=12)
        return response.json().get("tasks", []) if response.ok else []

    def complete_task(self, task_id: str, result: str) -> bool:
        path, body = "/api/bridge/tasks", json.dumps({"task_id": task_id, "result": result[:1000]})
        response = requests.put(self.cfg.url + path, data=body, headers=self._signed_headers("PUT", path, body), timeout=12)
        return response.ok

    def sync_memory(self, payload: dict) -> dict | None:
        if not self.configured or not self.cfg.access_token:
            return None
        response = requests.put(self.cfg.url + "/api/memory", json=payload,
            headers={"Authorization": f"Bearer {self.cfg.access_token}"}, timeout=12)
        return response.json() if response.ok or response.status_code == 409 else None

    def sync_cloud_safe(self) -> str:
        """Sync only explicitly selected safe facts; stop on a revision conflict."""
        from memory.cloud_safe import export
        payload = export(self.cfg.cloud_memory_revision)
        if not payload["facts"]:
            return "empty"
        reply = self.sync_memory(payload)
        if not reply:
            return "unavailable"
        if reply.get("error") == "sync_conflict":
            return "conflict"
        revision = int(reply.get("revision", self.cfg.cloud_memory_revision))
        self.cfg.cloud_memory_revision = revision
        _patch_config(cloud_bridge=self.cfg.__dict__)
        return "synced"
