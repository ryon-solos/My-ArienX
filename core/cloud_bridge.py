"""Optional ArienX Cloud Core bridge. Private signing and Identity secrets stay local."""

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


_TASK_ACTIONS = {"system_status", "screen_process", "close_camera", "open_app", "browser_control", "file_controller", "computer_control"}


class BridgeError(RuntimeError):
    """A safe, user-facing Cloud Core failure. Never contains a credential."""


@dataclass
class BridgeConfig:
    url: str = ""
    access_token: str = ""
    refresh_token: str = ""
    token_expires_at: int = 0
    user_email: str = ""
    device_id: str = ""
    private_key_pem: str = ""
    device_label: str = "ArienX desktop"
    cloud_memory_revision: int = 0
    enabled: bool = False


def get_config() -> BridgeConfig:
    raw = load_api_keys().get("cloud_bridge")
    raw = raw if isinstance(raw, dict) else {}
    defaults = BridgeConfig()
    return BridgeConfig(**{key: raw.get(key, getattr(defaults, key)) for key in BridgeConfig.__annotations__})


def _save(cfg: BridgeConfig) -> None:
    _patch_config(cloud_bridge=cfg.__dict__)


def forget_local_pairing() -> None:
    """Remove local tokens and the local private key; no cloud state is changed."""
    _patch_config(cloud_bridge={})


def provision(url: str, email: str, password: str, label: str = "ArienX desktop") -> BridgeConfig:
    """Authenticate and pair without asking the user to copy a token or key."""
    endpoint = str(url or "").strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise BridgeError("Cloud URL must use HTTPS")
    if not str(email or "").strip() or not password:
        raise BridgeError("Enter your Netlify Identity email and password")
    previous = get_config()
    same_site = previous.url == endpoint and bool(previous.device_id)
    key = Ed25519PrivateKey.generate()
    cfg = BridgeConfig(
        url=endpoint,
        user_email=str(email).strip()[:160],
        device_id=previous.device_id if same_site else uuid.uuid4().hex,
        private_key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        device_label=str(label or "ArienX desktop").strip()[:80] or "ArienX desktop",
        cloud_memory_revision=previous.cloud_memory_revision if same_site else 0,
        enabled=True,
    )
    bridge = CloudBridge(cfg)
    if not bridge.authenticate(email, password):
        raise BridgeError(bridge.last_error or "Could not sign in to Cloud Core")
    if not bridge.register(cfg.device_label):
        raise BridgeError(bridge.last_error or "Could not pair this device")
    _save(cfg)
    return cfg


class CloudBridge:
    """Short HTTPS calls only; Netlify never receives a device private key."""

    def __init__(self, cfg: BridgeConfig | None = None):
        self.cfg = cfg or get_config()
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.url and self.cfg.device_id and self.cfg.private_key_pem)

    def _fail(self, message: str) -> bool:
        self.last_error = message
        return False

    def _identity_url(self, grant_type: str) -> str:
        return f"{self.cfg.url}/.netlify/identity/token?grant_type={grant_type}"

    def _set_tokens(self, data: dict[str, Any]) -> bool:
        access = str(data.get("access_token") or "")
        refresh = str(data.get("refresh_token") or self.cfg.refresh_token or "")
        if not access or not refresh:
            return self._fail("Cloud Core sign-in did not return a session")
        self.cfg.access_token = access
        self.cfg.refresh_token = refresh
        self.cfg.token_expires_at = int(time.time()) + max(60, int(data.get("expires_in") or 3600))
        return True

    def authenticate(self, email: str, password: str) -> bool:
        """Exchange local credentials for a local-only Netlify Identity session."""
        try:
            response = requests.post(self._identity_url("password"), json={"email": str(email).strip(), "password": password}, timeout=12)
            if not response.ok:
                return self._fail("Cloud Core sign-in failed. Check your email, password, and invite status.")
            return self._set_tokens(response.json())
        except (requests.RequestException, ValueError):
            return self._fail("Cloud Core is unavailable. Check the deployment URL and connection.")

    def _refresh_session(self) -> bool:
        if not self.cfg.refresh_token:
            return self._fail("Cloud Core sign-in has expired. Re-pair this device from Settings.")
        try:
            response = requests.post(self._identity_url("refresh_token"), json={"refresh_token": self.cfg.refresh_token}, timeout=12)
            if not response.ok or not self._set_tokens(response.json()):
                return self._fail("Cloud Core sign-in has expired. Re-pair this device from Settings.")
            _save(self.cfg)
            return True
        except (requests.RequestException, ValueError):
            return self._fail("Cloud Core session refresh failed. Try again shortly.")

    def _user_headers(self) -> dict[str, str] | None:
        if not self.cfg.access_token or self.cfg.token_expires_at - time.time() < 90:
            if not self._refresh_session():
                return None
        return {"Cookie": f"nf_jwt={self.cfg.access_token}; nf_refresh={self.cfg.refresh_token}", "Content-Type": "application/json"}

    def _signed_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        key = serialization.load_pem_private_key(self.cfg.private_key_pem.encode(), password=None)
        stamp = str(int(time.time()))
        digest = hashlib.sha256(body.encode()).hexdigest()
        signed = f"{stamp}\n{method}\n{path}\n{digest}".encode()
        return {"X-ArienX-Device": self.cfg.device_id, "X-ArienX-Time": stamp,
                "X-ArienX-Signature": base64.b64encode(key.sign(signed)).decode(), "Content-Type": "application/json"}

    def register(self, label: str | None = None) -> bool:
        headers = self._user_headers()
        if not self.configured or not headers:
            return False
        try:
            key = serialization.load_pem_private_key(self.cfg.private_key_pem.encode(), password=None)
            public_key = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            body = {"device_id": self.cfg.device_id, "label": (label or self.cfg.device_label)[:80], "public_key": public_key}
            response = requests.post(self.cfg.url + "/api/bridge/register", json=body, headers=headers, timeout=12)
            if not response.ok:
                return self._fail("Cloud Core rejected this device pairing. Re-pair from Settings.")
            self.cfg.device_label = body["label"]
            self.last_error = ""
            return True
        except (ValueError, requests.RequestException):
            return self._fail("Cloud Core pairing is unavailable. Try again shortly.")

    def heartbeat(self, capabilities: list[str]) -> bool:
        if not self.configured:
            return self._fail("Cloud Core is not paired")
        path, body = "/api/bridge/heartbeat", json.dumps({"capabilities": capabilities}, separators=(",", ":"))
        try:
            response = requests.post(self.cfg.url + path, data=body, headers=self._signed_headers("POST", path, body), timeout=8)
            if not response.ok:
                return self._fail("Cloud Core heartbeat was rejected. Re-pair this device.")
            self.last_error = ""
            return True
        except requests.RequestException:
            return self._fail("Cloud Core is offline or unreachable")

    def status(self) -> dict[str, Any] | None:
        headers = self._user_headers()
        if not headers or not self.cfg.device_id:
            return None
        try:
            response = requests.get(self.cfg.url + "/api/bridge/status", params={"device_id": self.cfg.device_id}, headers=headers, timeout=10)
            if response.ok:
                self.last_error = ""
                return response.json()
            self._fail("Cloud Core could not read device status")
        except (requests.RequestException, ValueError):
            self._fail("Cloud Core is offline or unreachable")
        return None

    def poll_tasks(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        path = "/api/bridge/tasks"
        try:
            response = requests.get(self.cfg.url + path, headers=self._signed_headers("GET", path), timeout=12)
            if response.ok:
                return response.json().get("tasks", [])
            self._fail("Cloud task connection was rejected. Re-pair this device.")
        except (requests.RequestException, ValueError):
            self._fail("Cloud Core is offline or unreachable")
        return []

    def queue_task(self, action: str, args: dict[str, Any] | None = None) -> str | None:
        if action not in _TASK_ACTIONS or not isinstance(args or {}, dict):
            self._fail("That cloud task is not allowed")
            return None
        headers = self._user_headers()
        if not headers:
            return None
        try:
            response = requests.post(self.cfg.url + "/api/bridge/tasks", json={"device_id": self.cfg.device_id, "action": action, "args": args or {}}, headers=headers, timeout=12)
            if response.ok:
                return str(response.json().get("task_id") or "") or None
            self._fail("Cloud Core could not queue that task")
        except (requests.RequestException, ValueError):
            self._fail("Cloud Core is offline or unreachable")
        return None

    def complete_task(self, task_id: str, result: str) -> bool:
        if not self.configured or not task_id:
            return False
        path, body = "/api/bridge/tasks", json.dumps({"task_id": task_id, "result": result[:1000]}, separators=(",", ":"))
        try:
            return requests.put(self.cfg.url + path, data=body, headers=self._signed_headers("PUT", path, body), timeout=12).ok
        except requests.RequestException:
            return False

    def cloud_chat(self, message: str, provider: str = "gemini") -> str | None:
        headers = self._user_headers()
        if not headers:
            return None
        try:
            response = requests.post(self.cfg.url + "/api/chat", json={"message": str(message)[:12_000], "provider": provider}, headers=headers, timeout=60)
            if response.ok:
                return str(response.json().get("reply") or "")
            self._fail("Cloud chat is unavailable")
        except (requests.RequestException, ValueError):
            self._fail("Cloud Core is offline or unreachable")
        return None

    def sync_memory(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        headers = self._user_headers()
        if not headers:
            return None
        try:
            response = requests.put(self.cfg.url + "/api/memory", json=payload, headers=headers, timeout=12)
            return response.json() if response.ok or response.status_code == 409 else None
        except (requests.RequestException, ValueError):
            self._fail("Cloud-safe memory is unavailable")
            return None

    def sync_cloud_safe(self) -> str:
        """Sync only explicit safe facts; stale writers never overwrite cloud data."""
        from memory.cloud_safe import export
        payload = export(self.cfg.cloud_memory_revision)
        if not payload["facts"]:
            return "empty"
        reply = self.sync_memory(payload)
        if not reply:
            return "unavailable"
        if reply.get("error") == "sync_conflict":
            return "conflict"
        self.cfg.cloud_memory_revision = int(reply.get("revision", self.cfg.cloud_memory_revision))
        _save(self.cfg)
        return "synced"

    def disconnect(self) -> bool:
        """Revoke the cloud record, then erase all local bridge secrets."""
        headers = self._user_headers()
        if not headers:
            return False
        try:
            response = requests.delete(self.cfg.url + "/api/bridge/register", params={"device_id": self.cfg.device_id}, headers=headers, timeout=12)
            if not response.ok:
                return self._fail("Cloud Core could not disconnect this device")
            forget_local_pairing()
            self.cfg = BridgeConfig()
            self.last_error = ""
            return True
        except requests.RequestException:
            return self._fail("Cloud Core is offline. This device is still paired remotely.")
