"""Lead-only natural-language lifecycle action for verified extension apps."""
import json
import re
from core import confirm
from extensions.runtime import get_runtime
from extensions.scaffold import files_for

def _extension_id(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:56]
    return cleaned if len(cleaned) >= 2 else "app-" + (cleaned or "new")

def extension_request(parameters: dict, player=None, **_kwargs) -> str:
    request = str((parameters or {}).get("request") or "").strip()
    operation = str((parameters or {}).get("operation") or "create").lower()
    app_id = _extension_id(str((parameters or {}).get("id") or ""))
    runtime = get_runtime()
    try:
        if operation == "list":
            apps = runtime.list(); return "Installed apps: " + (", ".join(f"{a.name} ({a.status})" for a in apps) or "none")
        if operation == "open":
            return f"Opened extension process {runtime.launch(app_id)}."
        if operation == "disable": runtime.set_enabled(app_id, False); return f"{app_id} disabled."
        if operation == "enable": runtime.set_enabled(app_id, True); return f"{app_id} enabled."
        if operation == "remove": runtime.uninstall(app_id); return f"{app_id} removed."
        if operation == "rollback": return f"Rollback {'completed' if runtime.rollback(app_id) else 'is unavailable'} for {app_id}."
        supplied = (parameters or {}).get("files")
        generated_id, files = files_for(request, str(parameters.get("name") or ""))
        if isinstance(supplied, dict) and supplied:
            files = {str(path): str(content) for path, content in supplied.items()}
            manifest = json.loads(files.get("manifest.json", "{}"))
            generated_id = _extension_id(str(manifest.get("id") or generated_id))
            manifest["id"] = generated_id
            files["manifest.json"] = json.dumps(manifest, indent=2)
        if operation == "update":
            manifest = json.loads(files["manifest.json"])
            manifest["id"] = app_id
            files["manifest.json"] = json.dumps(manifest, indent=2)
            info = runtime.update(app_id, files); return f"Updated {info.name} to {info.version}."
        staged = runtime.stage(generated_id, files)
        manifest = json.loads(files["manifest.json"])
        requested = [p for p in manifest.get("permissions", []) if p != "storage"]
        if requested:
            return confirm.request("extension-install", "Extension permission request", f"{manifest.get('name', generated_id)} requests: {', '.join(requested)}.", lambda: f"Installed {runtime.install(staged).name}.")
        info = runtime.install(staged)
        if player: player.show_extensions()
        return f"Installed {info.name} {info.version}. Open it from Apps."
    except Exception as exc:
        return f"Extension {operation} failed safely: {str(exc)[:1200]}"

TOOL = {"name": "extension_request", "description": "Lead-only extension lifecycle. For a requested app capability, first use multi_agent_task to generate the manifest/backend/declarative UI/tests/docs files in parallel, then call this tool with operation and optional files. It creates, updates, lists, opens, enables, disables, rolls back, or removes verified isolated extensions. Non-storage permissions require the on-screen confirmation gate.", "parameters": {"type": "OBJECT", "properties": {"operation": {"type": "STRING"}, "request": {"type": "STRING"}, "name": {"type": "STRING"}, "id": {"type": "STRING"}, "files": {"type": "OBJECT"}}, "required": ["operation"]}, "handler": extension_request}
