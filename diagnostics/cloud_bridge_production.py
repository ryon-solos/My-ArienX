"""Run the real Cloud Core lifecycle without printing credentials or session secrets.

Set ARIENX_EMAIL and ARIENX_PASSWORD in the local shell, then run this file from
the project root. It registers this desktop and prints only HTTP statuses and
redacted response fields.
"""

from __future__ import annotations

import os
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.cloud_bridge import BridgeConfig, BridgeError, CloudBridge, PRODUCTION_CLOUD_URL, _save


def main() -> None:
    email = os.environ.get("ARIENX_EMAIL", "").strip()
    password = os.environ.get("ARIENX_PASSWORD", "")
    if not email or not password:
        raise SystemExit("Set ARIENX_EMAIL and ARIENX_PASSWORD locally; they are never printed.")
    key = Ed25519PrivateKey.generate()
    cfg = BridgeConfig(
        url=PRODUCTION_CLOUD_URL,
        user_email=email[:160],
        device_id=uuid.uuid4().hex,
        private_key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        device_label="ArienX desktop",
        enabled=True,
    )
    bridge = CloudBridge(cfg)
    stages = (
        ("Cloud login", lambda: bridge.authenticate(email, password)),
        ("Authenticated session verification", bridge.verify_session),
        ("Desktop device registration", lambda: bridge.register(cfg.device_label)),
        ("Bridge status", lambda: bridge.status() is not None),
        ("Bridge heartbeat", lambda: bridge.heartbeat(["computer"])),
        ("Mobile pairing QR", lambda: bool(bridge.mobile_request("POST", "/api/mobile/pair", {}))),
    )
    for name, run in stages:
        if not run():
            raise BridgeError(bridge.last_error or f"{name} failed")
    _save(cfg)
    for entry in bridge.last_trace:
        print(f"{entry['stage']}: HTTP {entry['status']} {entry['body']}")
    print("Real Cloud Core lifecycle passed.")


if __name__ == "__main__":
    main()
