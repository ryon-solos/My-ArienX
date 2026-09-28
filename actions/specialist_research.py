"""Optional OpenAI-compatible specialist research action.

Only Gemini Live can call this action.  It is unavailable unless the user has
explicitly enabled and configured a provider in ArienX settings.
"""

import json
from urllib import request

from core.external_router import candidate_for
from memory.config_manager import get_external_providers


TOOL = {
    "name": "specialist_research",
    "description": (
        "Optional specialist analysis for difficult, detailed research, code, or "
        "reasoning tasks. Use only after deciding Gemini Live needs specialist "
        "help. It is not for ordinary questions or computer control."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING", "description": "The exact specialist task."},
            "role": {"type": "STRING", "description": "research, reasoning, or coding."},
        },
        "required": ["query"],
    },
}


def specialist_research(parameters: dict, **_kwargs) -> str:
    role = str(parameters.get("role") or "research").upper()
    candidate = candidate_for(role)
    if not candidate:
        return "No optional specialist provider is enabled; continue with Gemini and web_search."
    provider = candidate["provider"]
    config = get_external_providers()[provider]
    endpoint = ("https://openrouter.ai/api/v1/chat/completions" if provider == "openrouter"
                else config.get("base_url", "").rstrip("/") + "/chat/completions")
    payload = json.dumps({
        "model": candidate["model"],
        "messages": [
            {"role": "system", "content": "Provide a concise, factual specialist brief. State uncertainty; do not claim to have controlled a computer."},
            {"role": "user", "content": str(parameters["query"])[:8000]},
        ],
        "temperature": 0.2,
    }).encode("utf-8")
    req = request.Request(endpoint, data=payload, method="POST", headers={
        "Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json",
    })
    try:
        with request.urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        resolved_model = str(data.get("model") or candidate["model"])
        return (f"[SPECIALIST {provider.upper()} BRIEF — model={resolved_model}]\n"
                f"{str(content)[:12000]}")
    except Exception as exc:
        return f"Specialist provider failed ({type(exc).__name__}); continue with Gemini/web_search."


TOOL["handler"] = specialist_research
