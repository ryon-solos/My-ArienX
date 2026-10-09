"""Optional ArienX Cloud Core bridge. Private signing and Identity secrets stay local."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from memory.config_manager import _patch_config, load_api_keys


_TASK_ACTIONS = {"mobile_receive_file", "computer_settings", "extension_request", "system_status", "screen_process", "close_camera", "open_app", "browser_control", "file_controller", "computer_control"}
_SESSION_LOCK = threading.RLock()
_TOKEN_FIELDS = ("access_token", "refresh_token", "token_expires_at")

PRODUCTION_CLOUD_URL = "https://myarienx.netlify.app"


class BridgeError(RuntimeError):
    """A safe, user-facing Cloud Core failure. Never contains a credential."""


@dataclass
class BridgeConfig:
    url: str = PRODUCTION_CLOUD_URL
    access_token: str = ""
    refresh_token: str = ""
    token_expires_at: int = 0
    user_email: str = ""
    device_id: str = ""
    private_key_pem: str = ""
    device_label: str = "ArienX desktop"
    cloud_memory_revision: int = 0
    enabled: bool = False
    mobile_telemetry_enabled: bool = False
    cloud_memory_base: dict = field(default_factory=dict)


def get_config() -> BridgeConfig:
    raw = load_api_keys().get("cloud_bridge")
    raw = raw if isinstance(raw, dict) else {}
    defaults = BridgeConfig()
    values = {key: raw.get(key, getattr(defaults, key)) for key in BridgeConfig.__annotations__}
    values["url"] = str(values.get("url") or PRODUCTION_CLOUD_URL).strip().rstrip("/")
    return BridgeConfig(**values)


def _save(cfg: BridgeConfig, *, tokens_updated=False) -> None:
    with _SESSION_LOCK:
        current = get_config()
        # Other bridge clients may have renewed a rotating refresh token.
        if not tokens_updated and (current.url, current.device_id) == (cfg.url, cfg.device_id) and current.token_expires_at >= cfg.token_expires_at:
            for field in _TOKEN_FIELDS:
                setattr(cfg, field, getattr(current, field))
        if tokens_updated and (current.url, current.device_id) == (cfg.url, cfg.device_id):
            for field in _TOKEN_FIELDS:
                setattr(current, field, getattr(cfg, field))
            _patch_config(cloud_bridge=current.__dict__)
        else:
            _patch_config(cloud_bridge=cfg.__dict__)


def forget_local_pairing() -> None:
    """Remove local tokens and the local private key; no cloud state is changed."""
    with _SESSION_LOCK:
        _patch_config(cloud_bridge={})


def provision(email: str, password: str, label: str = "ArienX desktop", url_override: str = "") -> BridgeConfig:
    """Authenticate and pair without asking the user to copy a token or key."""
    endpoint = str(url_override or PRODUCTION_CLOUD_URL).strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise BridgeError("Cloud URL must use HTTPS")
    if not str(email or "").strip() or not password:
        raise BridgeError("Enter your email and password")
    previous = get_config()
    same_site = previous.url == endpoint and bool(previous.device_id)
    key = Ed25519PrivateKey.generate()
    cfg = BridgeConfig(
        url=endpoint,
        user_email=str(email).strip()[:160],
        device_id=uuid.uuid4().hex,
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
    if not bridge.verify_session():
        raise BridgeError(bridge.last_error or "Cloud Core session verification failed")
    if not bridge.register(cfg.device_label):
        raise BridgeError(bridge.last_error or "Could not pair this device")
    _save(cfg)
    return cfg


class CloudBridge:
    """Short HTTPS calls only; Netlify never receives a device private key."""

    def __init__(self, cfg: BridgeConfig | None = None):
        self.cfg = cfg or get_config()
        self.last_error = ""
        self.last_trace: list[dict[str, Any]] = []
        # Native clients use an independent, persistent cookie jar. The
        # configured tokens are restored into it after an app restart.
        self._session = requests.Session()
        self._restore_session_cookies()

    @property
    def configured(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.url and self.cfg.device_id and self.cfg.private_key_pem)

    def _fail(self, message: str) -> bool:
        self.last_error = message
        return False

    def _identity_url(self, grant_type: str) -> str:
        return f"{self.cfg.url}/.netlify/identity/token?grant_type={grant_type}"

    def _restore_session_cookies(self) -> None:
        host = urlparse(self.cfg.url).hostname
        if not host:
            return
        if self.cfg.access_token:
            self._session.cookies.set("nf_jwt", self.cfg.access_token, domain=host, path="/")
        if self.cfg.refresh_token:
            self._session.cookies.set("nf_refresh", self.cfg.refresh_token, domain=host, path="/")

    @staticmethod
    def _safe_body(response: Any) -> str:
        """Keep only non-secret response fields suitable for a user-visible error."""
        try:
            body = response.json()
        except (ValueError, TypeError):
            return ""
        if not isinstance(body, dict):
            return ""
        allowed = ("error", "message", "authenticated", "registered", "paired", "online", "re_paired")
        safe = {key: body[key] for key in allowed if key in body and isinstance(body[key], (str, bool, int, float))}
        return json.dumps(safe, separators=(",", ":"))[:300] if safe else ""

    def _record(self, stage: str, response: Any) -> int:
        status = int(getattr(response, "status_code", 0) or 0)
        self.last_trace.append({"stage": stage, "status": status, "body": self._safe_body(response)})
        del self.last_trace[:-32]
        return status

    def _response_failure(self, stage: str, response: Any, fallback: str) -> bool:
        status = self._record(stage, response)
        detail = self.last_trace[-1]["body"]
        return self._fail(f"{stage} failed ({status}): {detail or fallback}")

    def _set_tokens(self, data: dict[str, Any]) -> bool:
        access = str(data.get("access_token") or "")
        refresh = str(data.get("refresh_token") or self.cfg.refresh_token or "")
        token_type = str(data.get("token_type") or "")
        if not access or not refresh or token_type.lower() != "bearer":
            return self._fail("Cloud Core sign-in did not return a session")
        self.cfg.access_token = access
        self.cfg.refresh_token = refresh
        self.cfg.token_expires_at = int(time.time()) + max(60, int(data.get("expires_in") or 3600))
        self._restore_session_cookies()
        return True

    def authenticate(self, email: str, password: str) -> bool:
        """Use Netlify Identity's native password grant, never a browser Origin."""
        try:
            response = self._session.post(
                self.cfg.url + "/.netlify/identity/token",
                data={"grant_type": "password", "username": str(email).strip(), "password": password},
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                timeout=12,
            )
            if not response.ok:
                self._record("Cloud login", response)
                detail = self.last_trace[-1]["body"].lower()
                if "confirm" in detail:
                    return self._fail("Account not confirmed. Confirm the ArienX account email, then sign in.")
                if response.status_code in (400, 401):
                    return self._fail("Invalid email or password")
                return self._fail(
                    f"Cloud login failed ({response.status_code}): "
                    f"{self.last_trace[-1]['body'] or 'Cloud Core rejected the login'}"
                )
            self._record("Cloud login", response)
            if not self._set_tokens(response.json()):
                return False
            return True
        except (requests.RequestException, ValueError):
            return self._fail("Cloud Core is unavailable. Check the deployment URL and connection.")

    def verify_session(self) -> bool:
        """Confirm that the native session is accepted by Cloud Core before pairing."""
        headers = self._user_headers()
        if not headers:
            return False
        try:
            response = self._user_request("GET", self.cfg.url + "/api/auth/session", headers=headers, timeout=12)
            if not response.ok:
                return self._response_failure("Authenticated session verification", response, "session was not accepted")
            self._record("Authenticated session verification", response)
            try:
                if response.json().get("authenticated") is not True:
                    return self._fail("Authenticated session verification failed (200): session was not authenticated")
            except (ValueError, AttributeError):
                return self._fail("Authenticated session verification failed: invalid response")
            return True
        except (requests.RequestException, ValueError):
            return self._fail("Cloud Core is unavailable while verifying the native session")

    def _refresh_session(self) -> bool:
        if not self.cfg.refresh_token:
            return self._fail("Cloud Core needs sign-in. Connect this device from Settings.")
        try:
            response = self._session.post(
                self.cfg.url + "/.netlify/identity/token",
                data={"grant_type": "refresh_token", "refresh_token": self.cfg.refresh_token},
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                timeout=12,
            )
            if not response.ok:
                if response.status_code in (400, 401):
                    return self._fail("Cloud Core session was revoked. Sign in again from Settings.")
                return self._fail("Cloud Core session renewal is temporarily unavailable. It will retry automatically.")
            if not self._set_tokens(response.json()):
                return False
            _save(self.cfg, tokens_updated=True)
            self.last_error = ""
            return True
        except (requests.RequestException, ValueError):
            return self._fail("Cloud Core session renewal is temporarily unavailable. It will retry automatically.")

    def _user_headers(self) -> dict[str, str] | None:
        # Serialize refresh across desktop, conversation and settings clients.
        with _SESSION_LOCK:
            current = get_config()
            if (current.url, current.device_id) == (self.cfg.url, self.cfg.device_id) and current.refresh_token and current.token_expires_at >= self.cfg.token_expires_at:
                for field in _TOKEN_FIELDS:
                    setattr(self.cfg, field, getattr(current, field))
                self._restore_session_cookies()
            if not self.cfg.access_token or self.cfg.token_expires_at - time.time() < 90:
                if not self._refresh_session() and self.cfg.token_expires_at <= time.time():
                    return None
            return {
                "Authorization": f"Bearer {self.cfg.access_token}",
                "Cookie": f"nf_jwt={self.cfg.access_token}; nf_refresh={self.cfg.refresh_token}",
                "Content-Type": "application/json",
            }

    def _user_request(self, method: str, url: str, **kwargs):
        """Retry rejected bearer authentication once using the persisted refresh session."""
        response = self._session.request(method, url, **kwargs)
        if response.status_code != 401:
            return response
        rejected = (kwargs.get("headers") or {}).get("Authorization")
        with _SESSION_LOCK:
            headers = self._user_headers()
            if headers and headers.get("Authorization") == rejected:
                if not self._refresh_session():
                    return response
                headers = self._user_headers()
            if not headers:
                return response
        kwargs["headers"] = headers
        return self._session.request(method, url, **kwargs)

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
            response = self._user_request("POST", self.cfg.url + "/api/bridge/register", json=body, headers=headers, timeout=12)
            if not response.ok:
                return self._response_failure("Desktop device registration", response, "device registration was rejected")
            self._record("Desktop device registration", response)
            self.cfg.device_label = body["label"]
            self.last_error = ""
            return True
        except (ValueError, requests.RequestException):
            return self._fail("Cloud Core pairing is unavailable. Try again shortly.")

    def heartbeat(self, capabilities: list[str]) -> bool:
        if not self.configured:
            return self._fail("Cloud Core is not paired")
        self.last_trace = []
        path, body = "/api/bridge/heartbeat", json.dumps({"capabilities": capabilities}, separators=(",", ":"))
        try:
            response = self._session.post(self.cfg.url + path, data=body, headers=self._signed_headers("POST", path, body), timeout=8)
            if not response.ok:
                return self._response_failure("Bridge heartbeat", response, "heartbeat was rejected")
            self._record("Bridge heartbeat", response)
            self.last_error = ""
            return True
        except requests.RequestException:
            return self._fail("Cloud Core is offline or unreachable")

    def status(self) -> dict[str, Any] | None:
        headers = self._user_headers()
        if not headers or not self.cfg.device_id:
            return None
        try:
            response = self._user_request("GET", self.cfg.url + "/api/bridge/status", params={"device_id": self.cfg.device_id}, headers=headers, timeout=10)
            if response.ok:
                self._record("Bridge status", response)
                self.last_error = ""
                return response.json()
            self._response_failure("Bridge status", response, "device status was rejected")
        except (requests.RequestException, ValueError):
            self._fail("Cloud Core is offline or unreachable")
        return None

    def poll_tasks(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        path = "/api/bridge/tasks"
        try:
            response = self._session.get(self.cfg.url + path, headers=self._signed_headers("GET", path), timeout=12)
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
            response = self._user_request("POST", self.cfg.url + "/api/bridge/tasks", json={"device_id": self.cfg.device_id, "action": action, "args": args or {}}, headers=headers, timeout=12)
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
            return self._session.put(self.cfg.url + path, data=body, headers=self._signed_headers("PUT", path, body), timeout=12).ok
        except requests.RequestException:
            return False

    def cloud_chat(self, message: str, provider: str = "gemini") -> str | None:
        headers = self._user_headers()
        if not headers:
            return None
        try:
            response = self._user_request("POST", self.cfg.url + "/api/chat", json={"message": str(message)[:12_000], "provider": provider}, headers=headers, timeout=60)
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
            response = self._user_request("PUT", self.cfg.url + "/api/memory", json=payload, headers=headers, timeout=12)
            return response.json() if response.ok or response.status_code == 409 else None
        except (requests.RequestException, ValueError):
            self._fail("Cloud-safe memory is unavailable")
            return None

    def sync_cloud_safe(self) -> str:
        """Merge only explicit safe facts; preserve both sides on conflicts."""
        from memory.cloud_safe import project_long_term, load, merge_facts, accept_cloud, apply_to_long_term, valid_fact
        headers = self._user_headers()
        if not headers:
            return "unavailable"
        try:
            project_long_term()
            response = self._user_request("GET", self.cfg.url + "/api/memory", headers=headers, timeout=12)
            if not response.ok:
                return "unavailable"
            remote, local = response.json(), load()
            remote_facts = remote.get("facts", {})
            if not isinstance(remote_facts, dict):
                return "unavailable"
            if any(not isinstance(f, dict) or not valid_fact(str(f.get("category", "")), str(f.get("key", "")), str(f.get("value", ""))) for f in remote_facts.values()):
                return "unavailable"
            merged, conflicts = merge_facts(self.cfg.cloud_memory_base or {}, local["facts"], remote_facts)
            if conflicts:
                return "conflict"
            changed = [fact for key, fact in merged.items() if remote_facts.get(key) != fact]
            for offset in range(0, len(changed), 100):
                result = self.sync_memory({"base_revision": remote["revision"], "facts": changed[offset:offset + 100]})
                if not result:
                    return "unavailable"
                if result.get("error") == "sync_conflict":
                    return "conflict"
                remote = result
            if not accept_cloud(merged, local["revision"]):
                return "conflict"
            apply_to_long_term(merged)
            self.cfg.cloud_memory_base = merged
            self.cfg.cloud_memory_revision = int(remote["revision"])
            _save(self.cfg)
            return "synced"
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return "unavailable"

    def disconnect(self) -> bool:
        """Revoke the cloud record, then erase all local bridge secrets."""
        headers = self._user_headers()
        if not headers:
            return False
        try:
            response = self._user_request("DELETE", self.cfg.url + "/api/bridge/register", params={"device_id": self.cfg.device_id}, headers=headers, timeout=12)
            if not response.ok:
                return self._fail("Cloud Core could not disconnect this device")
            forget_local_pairing()
            self.cfg = BridgeConfig()
            self.last_error = ""
            return True
        except requests.RequestException:
            return self._fail("Cloud Core is offline. This device is still paired remotely.")

    def mobile_request(self, method: str, path: str, body: dict | None = None) -> dict:
        if not path.startswith(("/api/mobile/", "/api/conversations")):
            raise BridgeError("Unsupported mobile operation")
        headers = self._user_headers()
        if not headers:
            raise BridgeError(self.last_error or "Pair Cloud Core first")
        try:
            response = self._user_request(method, self.cfg.url + path, headers=headers, json=body, timeout=15)
            if not response.ok:
                self._response_failure("Mobile pairing" if path == "/api/mobile/pair" else "Cloud mobile request", response, "request was rejected")
                raise BridgeError(self.last_error)
            self._record("Mobile pairing" if path == "/api/mobile/pair" else "Cloud mobile request", response)
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if isinstance(exc, BridgeError):
                raise
            raise BridgeError("Cloud mobile service is unreachable") from exc

    @staticmethod
    def _conversation_messages(messages: Any) -> list[dict[str, str]]:
        return [
            {"role": str(message.get("role")), "text": str(message.get("text"))}
            for message in messages or [] if isinstance(message, dict)
            and message.get("role") in ("user", "assistant") and str(message.get("text") or "").strip()
        ][-160:]

    def sync_conversation(self, local: dict) -> dict | None:
        """Merge one Desktop conversation into the existing account store.

        Only prefix extensions are written. Divergent histories stay local and
        report a conflict rather than silently replacing a phone conversation.
        """
        chat_id = str(local.get("id") or "")
        if not chat_id:
            return None
        headers = self._user_headers()
        if not headers:
            return None
        body = {"id": chat_id, "title": str(local.get("title") or "New conversation"),
                "messages": self._conversation_messages(local.get("messages"))}
        try:
            created = self._user_request("POST", self.cfg.url + "/api/conversations", headers=headers, json=body, timeout=12)
            if created.status_code == 201:
                return created.json()
            if created.status_code != 409:
                self._response_failure("Conversation sync", created, "conversation was rejected")
                return None
            remote_response = self._user_request("GET", self.cfg.url + "/api/conversations", params={"id": chat_id}, headers=headers, timeout=12)
            if not remote_response.ok:
                self._response_failure("Conversation sync", remote_response, "could not read cloud conversation")
                return None
            remote = remote_response.json()
            remote_messages, local_messages = self._conversation_messages(remote.get("messages")), body["messages"]
            if remote_messages == local_messages:
                return remote
            if remote_messages[:len(local_messages)] == local_messages:
                return remote
            if local_messages[:len(remote_messages)] != remote_messages:
                self._fail("Conversation sync paused: the same chat changed on another device")
                return None
            response = self._user_request("PATCH", self.cfg.url + "/api/conversations", headers=headers,
                                           json={**body, "revision": remote.get("revision")}, timeout=12)
            if not response.ok:
                self._response_failure("Conversation sync", response, "conversation changed; refresh first")
                return None
            return response.json()
        except (requests.RequestException, ValueError):
            self._fail("Cloud conversations are unavailable")
            return None

    def cloud_conversations(self) -> list[dict]:
        try:
            listing = self.mobile_request("GET", "/api/conversations")
            return [self.mobile_request("GET", "/api/conversations?id=" + str(row.get("id") or ""))
                    for row in listing.get("conversations", []) if row.get("id")]
        except BridgeError:
            return []

    def publish_mobile_snapshot(self) -> bool:
        from core.mobile_bridge import snapshot
        if not self.configured or not get_config().mobile_telemetry_enabled:
            return False
        try:
            path = "/api/bridge/telemetry"
            body = json.dumps(snapshot(), separators=(",", ":"))
            return self._session.post(self.cfg.url + path, data=body,
                                 headers=self._signed_headers("POST", path, body), timeout=8).ok
        except (requests.RequestException, ValueError, OSError):
            return False
