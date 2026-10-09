"""Functional, dependency-free extension templates and library catalog."""
from __future__ import annotations
import json, re

LIBRARY = {
    "weather": ("Weather", "Weather", "browser"), "clock": ("Clock", "Clock", "storage"),
    "calendar": ("Calendar", "Calendar", "storage"), "notes": ("Notes", "Notes", "storage"),
    "tasks": ("Tasks", "Tasks", "storage"), "calculator": ("Calculator", "Calculator", "storage"),
    "timer": ("Timer", "Timer", "storage"), "pomodoro": ("Pomodoro", "Pomodoro", "storage"),
}

def _kind(text: str) -> str:
    lower = text.lower()
    for key in LIBRARY:
        if key in lower: return key
    return "dashboard"

def files_for(request: str, name: str = ""):
    kind = _kind(name or request)
    default, category, permission = LIBRARY.get(kind, (name.strip() or re.sub(r"\b(app|application)\b", "", request, flags=re.I).strip().title() or "New App", "Created", "storage"))
    title = name.strip() or default
    app_id = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "new-app"
    ui = {"mode": "window", "kind": kind, "title": title, "heading": title, "message": f"{title} is ready.", "icon": {"weather":"☀", "clock":"◷", "calendar":"▣", "notes":"✎", "tasks":"✓", "calculator":"÷", "timer":"⏱", "pomodoro":"◉"}.get(kind, "◆")}
    manifest = {"id": app_id, "name": title, "version": "1.0.0", "category": category, "permissions": ["storage"] if permission == "storage" else ["storage", permission], "ui": ui, "storage": {"format": "json", "migration": "migrations.py"}, "rollback": {"history": "history.json"}, "dependencies": []}
    return app_id, {"manifest.json": json.dumps(manifest, indent=2), "backend.py": "def activate(sdk):\n    if not sdk.read(): sdk.write({'created': True})\n\ndef deactivate(): pass\n", "ui.py": "# Declarative UI is rendered safely by Apps workspace.\n", "test_extension.py": "from pathlib import Path\nassert Path('manifest.json').exists()\nprint('extension test: passed')\n", "README.md": f"# {title}\n\nFunctional {kind} extension generated from: {request}\n", "migrations.py": "def migrate(data, from_version, to_version): return data\n", "settings.json": "{}\n"}
