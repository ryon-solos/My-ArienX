"""Offline Phase 18 bridge lifecycle check; no network, credentials, or user files."""

import base64
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import cloud_bridge as bridge_module
from core.cloud_bridge import BridgeConfig, CloudBridge
from memory import cloud_safe


class _Response:
    def __init__(self, data, status=200, cookies=None): self._data, self.status_code, self.cookies = data, status, cookies or {}
    @property
    def ok(self): return 200 <= self.status_code < 300
    def json(self): return self._data


class _Requests:
    RequestException = Exception
    class _Cookies:
        def set(self, *_args, **_kwargs): pass
    def __init__(self): self.calls = []; self.cookies = self._Cookies()
    def Session(self): return self
    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/.netlify/identity/token"):
            return _Response({"access_token": "access", "refresh_token": "refresh", "expires_in": 3600, "token_type": "bearer"})
        if url.endswith("/api/chat"): return _Response({"reply": "cloud reply"})
        if url.endswith("/api/bridge/tasks"): return _Response({"task_id": "queued"})
        return _Response({"registered": True, "online": True})
    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/api/auth/session"): return _Response({"authenticated": True})
        if url.endswith("/api/bridge/tasks"): return _Response({"tasks": [{"id": "t1", "action": "system_status", "args": {}}]})
        return _Response({"online": True, "paired": True})
    def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        if url.endswith("/api/memory"): return _Response({"revision": 1})
        return _Response({"complete": True})
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/api/mobile/pair"):
            return _Response({"qr": "arienx://pair?cloud=https%3A%2F%2Fcloud.example&code=" + "a" * 64, "expires": 1})
        return getattr(self, method.lower())(url, **kwargs)
    def delete(self, url, **kwargs): self.calls.append(("DELETE", url, kwargs)); return _Response({"disconnected": True})


def main() -> None:
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    fake, original_requests, original_forget = _Requests(), bridge_module.requests, bridge_module.forget_local_pairing
    try:
        bridge_module.requests = fake
        bridge_module.forget_local_pairing = lambda: None
        cfg = BridgeConfig(url="https://cloud.example", device_id="a" * 32, private_key_pem=pem, enabled=True)
        bridge = CloudBridge(cfg)
        headers = bridge._signed_headers("POST", "/api/bridge/heartbeat", "{}")
        assert headers["X-ArienX-Device"] == "a" * 32 and "PRIVATE KEY" not in str(headers)
        assert base64.b64decode(headers["X-ArienX-Signature"])
        assert bridge.authenticate("user@example.test", "password")
        login = fake.calls[-1]
        assert login[1].endswith("/.netlify/identity/token")
        assert login[2]["data"] == {"grant_type": "password", "username": "user@example.test", "password": "password"}
        assert login[2]["headers"] == {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
        assert bridge.verify_session()
        assert bridge.register("Test device")
        assert bridge.status() == {"online": True, "paired": True}
        assert bridge.heartbeat(["screen"])
        issued = bridge.mobile_request("POST", "/api/mobile/pair", {})
        assert issued["qr"].startswith("arienx://pair?")
        assert bridge.cloud_chat("Hello") == "cloud reply"
        assert bridge.sync_memory({"base_revision": 0, "facts": [{"category": "notes", "key": "project", "value": "ArienX"}]}) == {"revision": 1}
        assert bridge.queue_task("system_status", {}) == "queued"
        assert bridge.queue_task("not_allowed", {}) is None
        assert bridge.poll_tasks()[0]["id"] == "t1"
        assert bridge.complete_task("t1", "done")
        assert bridge.disconnect()
        reconnect = CloudBridge(cfg)
        assert reconnect.authenticate("user@example.test", "password") and reconnect.register() and reconnect.heartbeat(["screen"])
        assert pem not in str(fake.calls) and "password" not in str(bridge.last_trace)
        assert [call[0] for call in fake.calls].count("POST") >= 6
    finally:
        bridge_module.requests, bridge_module.forget_local_pairing = original_requests, original_forget

    old_path = cloud_safe.PATH
    try:
        with tempfile.TemporaryDirectory() as directory:
            cloud_safe.PATH = Path(directory) / "cloud_safe.json"
            saved = cloud_safe.upsert("notes", "project", "ArienX Cloud Core")
            assert saved["revision"] == 1 and len(saved["facts"]) == 1
            try:
                cloud_safe.upsert("notes", "api_key", "not allowed")
                raise AssertionError("secret accepted")
            except ValueError:
                pass
    finally:
        cloud_safe.PATH = old_path
    print("Cloud bridge lifecycle diagnostics passed")


if __name__ == "__main__":
    main()
