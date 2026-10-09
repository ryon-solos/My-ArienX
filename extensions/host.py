"""Runs only inside Bubblewrap; the ArienX core never imports extension code."""
from __future__ import annotations
import argparse, importlib.util, json, sys
from pathlib import Path

class SDK:
    def __init__(self, root, permissions): self.root, self.permissions = Path(root), frozenset(permissions)
    def require(self, permission):
        if permission not in self.permissions: raise PermissionError(f"Permission denied: {permission}")
    def read(self):
        try: return json.loads((self.root / "storage.json").read_text(encoding="utf-8"))
        except Exception: return {}
    def write(self, value): self.require("storage"); (self.root / "storage.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
    def request(self, capability, payload=None): self.require(capability); return {"capability": capability, "payload": payload or {}}

def _load(root, name):
    spec = importlib.util.spec_from_file_location(f"app_{root.name}_{name}", root / f"{name}.py")
    if not spec or not spec.loader: raise RuntimeError("Invalid extension module")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("root"); parser.add_argument("--ui", action="store_true"); parser.add_argument("--test", action="store_true"); args = parser.parse_args()
    try:
        import resource; resource.setrlimit(resource.RLIMIT_CPU, (30, 30)); resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except Exception: pass
    root = Path(args.root); manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8")); sdk = SDK(root, manifest.get("permissions", []))
    if args.test:
        import runpy; runpy.run_path(str(root / "test_extension.py")); return
    backend = _load(root, "backend"); getattr(backend, "activate", lambda _sdk: None)(sdk)
    if not args.ui: print('{"ok": true}'); return
    ui = _load(root, "ui"); app_widget = ui.build(None, sdk); app_widget.show()
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv); sys.exit(app.exec())
if __name__ == "__main__": main()
