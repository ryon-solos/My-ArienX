"""Offline Cloud Core bridge checks; no network, credentials, or user files."""

import base64
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import cloud_bridge as bridge_module
from core.cloud_bridge import BridgeConfig, CloudBridge
from memory import cloud_safe


def main() -> None:
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    bridge = CloudBridge(BridgeConfig(url="https://cloud.example", device_id="a" * 32, private_key_pem=pem, enabled=True))
    headers = bridge._signed_headers("POST", "/api/bridge/heartbeat", "{}")
    assert headers["X-ArienX-Device"] == "a" * 32 and "PRIVATE KEY" not in str(headers)
    assert base64.b64decode(headers["X-ArienX-Signature"])

    class _Response:
        ok = True; status_code = 200
        def json(self): return {"tasks": [{"id": "t1", "action": "system_status", "args": {}}]}
    class _Requests:
        def __init__(self): self.calls = []
        def post(self, url, **kwargs): self.calls.append(("POST", url, kwargs)); return _Response()
        def get(self, url, **kwargs): self.calls.append(("GET", url, kwargs)); return _Response()
        def put(self, url, **kwargs): self.calls.append(("PUT", url, kwargs)); return _Response()
    fake, original_requests = _Requests(), bridge_module.requests
    try:
        bridge_module.requests = fake
        assert bridge.heartbeat(["screen"])
        assert bridge.poll_tasks()[0]["id"] == "t1"
        assert bridge.complete_task("t1", "done")
        assert [call[0] for call in fake.calls] == ["POST", "GET", "PUT"]
        assert not CloudBridge(BridgeConfig()).heartbeat(["screen"])
    finally:
        bridge_module.requests = original_requests

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
    print("Cloud bridge diagnostics passed")


if __name__ == "__main__":
    main()
