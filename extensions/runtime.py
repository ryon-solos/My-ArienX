"""Verified lifecycle runtime. Bubblewrap is mandatory: no unsafe fallback."""
from __future__ import annotations
import ast, json, re, shutil, subprocess, sys, time
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# Extensions are user data, never source-checkout files. This also prevents an
# app build from modifying the ArienX installation itself.
APPS_DIR = Path.home() / ".local" / "share" / "ArienX" / "extensions"
ALLOWED_PERMISSIONS = frozenset({"storage", "notifications", "scheduling", "browser", "cloud", "filesystem", "camera", "screen", "voice", "workers"})
REQUIRED = ("manifest.json", "backend.py", "ui.py", "test_extension.py", "README.md", "migrations.py", "settings.json")
FORBIDDEN_IMPORTS = {"core", "main", "memory", "os", "subprocess", "socket", "urllib", "requests", "shutil", "ctypes"}; FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input"}; _ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
@dataclass
class ExtensionInfo:
    id: str; name: str; version: str; category: str; enabled: bool; status: str; root: Path; diagnostics: list[str]
class ExtensionRuntime:
    def __init__(self, apps_dir=None): self.apps_dir = Path(apps_dir or APPS_DIR); self.apps_dir.mkdir(parents=True, exist_ok=True); self._running = {}
    def _root(self, app_id):
        if not _ID.fullmatch(app_id): raise ValueError("Invalid extension id.")
        return self.apps_dir / app_id
    @staticmethod
    def _read(path, fallback):
        try: return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception: return fallback
    def list(self):
        items = []
        for root in sorted(self.apps_dir.iterdir()):
            if not root.is_dir() or root.name.startswith("."): continue
            manifest = self._read(root / "manifest.json", None)
            if isinstance(manifest, dict):
                state = self._read(root / "state.json", {"enabled": True}); items.append(ExtensionInfo(root.name, str(manifest.get("name") or root.name), str(manifest.get("version") or "0.0.0"), str(manifest.get("category") or "Other"), bool(state.get("enabled", True)), "running" if root.name in self._running and self._running[root.name].poll() is None else "installed", root, self._read(root / "diagnostics.json", [])))
        return items
    def info(self, app_id):
        for item in self.list():
            if item.id == app_id: return item
        raise KeyError(app_id)
    def validate(self, root):
        root = Path(root); errors = [f"Missing {f}" for f in REQUIRED if not (root / f).is_file()]; manifest = self._read(root / "manifest.json", None)
        if not isinstance(manifest, dict): return errors + ["Invalid manifest JSON."]
        for key in ("id", "name", "version", "category", "permissions", "ui", "storage", "rollback", "dependencies"):
            if key not in manifest: errors.append(f"Manifest missing {key}.")
        if manifest.get("id") != root.name: errors.append("Manifest id must match extension directory.")
        if not isinstance(manifest.get("permissions"), list) or any(p not in ALLOWED_PERMISSIONS for p in manifest.get("permissions", [])): errors.append("Unsupported permission.")
        for source in (root / "backend.py", root / "ui.py", root / "migrations.py"):
            try:
                tree = ast.parse(source.read_text(encoding="utf-8"), str(source))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)) and any(a.name.split(".")[0] in FORBIDDEN_IMPORTS for a in node.names): errors.append(f"{source.name} imports a protected capability.")
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS: errors.append(f"{source.name} uses prohibited execution.")
            except SyntaxError as exc: errors.append(f"{source.name}:{exc.lineno}: {exc.msg}")
        if (root / "requirements.txt").exists() and (root / "requirements.txt").read_text(encoding="utf-8").strip(): errors.append("Third-party extension dependencies are not supported.")
        return errors
    def _sandbox(self, root, ui=False, test=False):
        bwrap = shutil.which("bwrap")
        if not bwrap: raise RuntimeError("Bubblewrap is required; refusing unsandboxed extension execution.")
        root = Path(root).resolve(); cmd = [bwrap, "--unshare-all", "--new-session", "--die-with-parent", "--proc", "/proc", "--dev", "/dev", "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/bin", "/bin", "--ro-bind", str(BASE_DIR / "host.py"), "/host.py", "--bind", str(root), "/app", "--chdir", "/app", "--tmpfs", "/tmp", "--setenv", "HOME", "/tmp", sys.executable, "-I", "/host.py", "/app"]
        if ui: cmd.append("--ui")
        if test: cmd.append("--test")
        return cmd
    def _record(self, root, event, detail=""):
        path = Path(root) / "history.json"; history = self._read(path, []); history.append({"at": int(time.time()), "event": event, "detail": detail}); path.write_text(json.dumps(history[-100:], indent=2), encoding="utf-8")
    def verify(self, root):
        root = Path(root); errors = self.validate(root)
        if not errors:
            try: test = subprocess.run(self._sandbox(root, test=True), capture_output=True, text=True, timeout=35)
            except Exception as exc: errors.append(f"Sandbox unavailable: {exc}")
            else:
                if test.returncode: errors.append((test.stderr or test.stdout or "Sandbox test failed")[-1200:])
            if not errors:
                try: probe = subprocess.run(self._sandbox(root), capture_output=True, text=True, timeout=35)
                except Exception as exc: errors.append(f"Sandbox unavailable: {exc}")
                else:
                    if probe.returncode: errors.append((probe.stderr or probe.stdout or "Sandbox execution failed")[-1200:])
        (root / "diagnostics.json").write_text(json.dumps(errors, indent=2), encoding="utf-8"); return errors
    def stage(self, app_id, files):
        root = self.apps_dir / ".staging" / app_id; shutil.rmtree(root, ignore_errors=True); root.mkdir(parents=True)
        for relative, content in files.items():
            path = (root / relative).resolve()
            if root.resolve() not in path.parents: raise ValueError("Invalid extension path.")
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(str(content), encoding="utf-8")
        return root
    def install(self, staged):
        staged = Path(staged); manifest = self._read(staged / "manifest.json", {}); app_id = str(manifest.get("id") or ""); destination = self._root(app_id); errors = self.verify(staged)
        if errors: raise ValueError("Verification failed: " + " | ".join(errors))
        if destination.exists(): self.uninstall(app_id)
        shutil.copytree(staged, destination); (destination / "state.json").write_text('{"enabled": true}', encoding="utf-8"); self._record(destination, "installed", str(manifest.get("version") or "")); return self.info(app_id)
    def update(self, app_id, files):
        root = self._root(app_id); backup = root.with_name("." + app_id + ".rollback")
        if not root.exists(): raise KeyError(app_id)
        self.unload(app_id); shutil.rmtree(backup, ignore_errors=True); shutil.copytree(root, backup); staged = self.stage(app_id, files); errors = self.verify(staged)
        if errors: raise ValueError("Verification failed: " + " | ".join(errors))
        storage = (root / "storage.json").read_bytes() if (root / "storage.json").exists() else None; shutil.rmtree(root); shutil.copytree(staged, root)
        if storage is not None: (root / "storage.json").write_bytes(storage)
        self._record(root, "updated"); return self.info(app_id)
    def rollback(self, app_id):
        root = self._root(app_id); backup = root.with_name("." + app_id + ".rollback")
        if not backup.exists(): return False
        self.unload(app_id); shutil.rmtree(root, ignore_errors=True); shutil.move(str(backup), str(root)); self._record(root, "rolled_back"); return True
    def set_enabled(self, app_id, enabled):
        root = self._root(app_id)
        if not enabled: self.unload(app_id)
        (root / "state.json").write_text(json.dumps({"enabled": bool(enabled)}), encoding="utf-8"); self._record(root, "enabled" if enabled else "disabled")
    def unload(self, app_id):
        process = self._running.pop(app_id, None)
        if process and process.poll() is None: process.terminate()
    def uninstall(self, app_id): self.unload(app_id); shutil.rmtree(self._root(app_id), ignore_errors=True)
    def launch(self, app_id):
        info = self.info(app_id)
        if not info.enabled: raise RuntimeError("Extension is disabled.")
        self.unload(app_id); process = subprocess.Popen(self._sandbox(info.root), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True); self._running[app_id] = process; self._record(info.root, "launched"); return process.pid
    def diagnostics(self, app_id): return self._read(self._root(app_id) / "diagnostics.json", [])
    def read_storage(self, app_id): return self._read(self._root(app_id) / "storage.json", {})
    def write_storage(self, app_id, value):
        root = self._root(app_id); (root / "storage.json").write_text(json.dumps(value or {}, indent=2), encoding="utf-8")
        self._record(root, "storage_updated")
    def export_extension(self, app_id, destination): return Path(shutil.make_archive(str(Path(destination).with_suffix("")), "zip", self._root(app_id)))
_RUNTIME = None
def get_runtime():
    global _RUNTIME
    if _RUNTIME is None: _RUNTIME = ExtensionRuntime()
    return _RUNTIME
