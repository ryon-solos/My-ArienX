"""Offline extension validation, lifecycle, rollback, and isolation diagnostics."""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from extensions.runtime import ExtensionRuntime
from extensions.scaffold import files_for

def main():
    with tempfile.TemporaryDirectory() as temp:
        runtime = ExtensionRuntime(Path(temp) / "apps")
        app_id, files = files_for("Build a Pomodoro app", "Pomodoro")
        staged = runtime.stage(app_id, files)
        assert not runtime.validate(staged), runtime.validate(staged)
        (staged / "backend.py").write_text("import core\n", encoding="utf-8")
        assert any("protected" in error for error in runtime.validate(staged))
        staged = runtime.stage(app_id, files)
        runtime.verify = lambda _root: []  # lifecycle test: sandbox is tested separately on host OS
        installed = runtime.install(staged); assert installed.enabled
        (installed.root / "storage.json").write_text('{"kept": true}', encoding="utf-8")
        manifest = runtime._read(installed.root / "manifest.json", {}); manifest["version"] = "0.2.0"; files["manifest.json"] = json.dumps(manifest)
        runtime.update(app_id, files)
        assert runtime._read(runtime._root(app_id) / "storage.json", {}) == {"kept": True}
        assert runtime.rollback(app_id)
        runtime.set_enabled(app_id, False); assert not runtime.info(app_id).enabled
        runtime.uninstall(app_id); assert not runtime.list()
    print("extension runtime diagnostics: passed")

if __name__ == "__main__": main()
