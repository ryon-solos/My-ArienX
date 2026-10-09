"""Secret-free status and on-demand validation for optional providers."""

from __future__ import annotations

import json
from urllib import request

from memory.config_manager import get_external_providers


def provider_status() -> list[dict]:
    """Safe UI/developer status; API key values never leave configuration."""
    rows = []
    for name, item in get_external_providers().items():
        rows.append({
            "provider": name,
            "enabled": bool(item.get("enabled")),
            "configured": bool(item.get("api_key")) and (name != "omnirouter" or bool(item.get("base_url"))),
            "model": str(item.get("model") or "auto"),
        })
    return rows


def test_openrouter_key(api_key: str) -> str:
    """Validate a user-supplied key on demand without logging or storing it."""
    if not (api_key or "").strip():
        return "No OpenRouter API key entered."
    req = request.Request("https://openrouter.ai/api/v1/key", headers={
        "Authorization": f"Bearer {api_key.strip()}",
    })
    try:
        with request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        label = str((data.get("data") or {}).get("label") or "configured key")
        return f"OpenRouter key accepted ({label})."
    except Exception as exc:
        return f"OpenRouter key test failed ({type(exc).__name__})."
