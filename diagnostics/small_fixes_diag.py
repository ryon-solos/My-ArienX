"""Offline regression checks: playback cancellation, deletion and long history.
Run: python3 -m diagnostics.small_fixes_diag
"""
import ast
import asyncio
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types

ROOT = Path(__file__).resolve().parents[1]


def load_writer():
    tree = ast.parse((ROOT / "main.py").read_text())
    node = next(n for n in tree.body if getattr(n, "name", "") == "_write_audio_safely")
    scope = {"asyncio": asyncio}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), scope)
    return scope["_write_audio_safely"]


async def playback():
    entered, release = threading.Event(), threading.Event()
    class Stream:
        finished = False
        def write(self, data):
            entered.set()
            assert release.wait(2), "test writer timed out"
            self.finished = True
    stream = Stream()
    task = asyncio.create_task(load_writer()(stream, b"pcm"))
    await asyncio.to_thread(entered.wait, 1)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done(), "cancellation must wait for the native write before closing"
    release.set()
    try:
        await task
        raise AssertionError("cancellation was swallowed")
    except asyncio.CancelledError:
        assert stream.finished


def storage():
    # Load only ChatStore; do not read the user's memory or import app services.
    with tempfile.TemporaryDirectory() as directory:
        parent = types.ModuleType("fixcheck")
        parent.__path__ = []
        memory = types.ModuleType("fixcheck.memory_manager")
        memory.get_base_dir = lambda: Path(directory)
        sys.modules["fixcheck"] = parent
        sys.modules["fixcheck.memory_manager"] = memory
        spec = importlib.util.spec_from_file_location("fixcheck.chat_store", ROOT / "memory/chat_store.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        store = module.ChatStore()
        chat = store.create()
        text = "Detailed answer\n\n" + "evidence " * 1500
        store.append(chat["id"], "assistant", text)
        assert store.messages(chat["id"])[0]["text"] == text.strip()
        stale = store.conversation(chat["id"])
        assert store.delete(chat["id"], cloud=True)
        assert store.accept_cloud(stale) is None, "sync resurrected a deleted chat"
        assert store.pending_cloud_deletes() == [chat["id"]]
        store.cloud_delete_done(chat["id"])
        assert not store.pending_cloud_deletes()
        assert module.ChatStore().accept_cloud(stale) is None, "deletion must survive restart"


def documents():
    tree = ast.parse((ROOT / "actions/file_controller.py").read_text())
    node = next(n for n in tree.body if getattr(n, "name", "") == "file_controller")
    captured = {}
    content = "Chapter\n\n" + "Complete detail. " * 1200
    fake = types.ModuleType("core.gemini")
    fake.SMART = "smart"
    def text(*args, **kwargs):
        captured.update(kwargs)
        return content
    fake.text = text
    previous = {name: sys.modules.get(name) for name in ("core", "core.gemini")}
    package = types.ModuleType("core")
    package.gemini = fake
    sys.modules.update({"core": package, "core.gemini": fake})
    try:
        scope = {"create_file": lambda path, name, content: content}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "file_controller.py", "exec"), scope)
        request = {"action": "create_file", "instruction": "Write in detail, 2000 words."}
        assert scope["file_controller"](request) == content
        assert captured["config"]["max_output_tokens"] == 16384
        assert captured["timeout_ms"] == 120000
        fake.text = lambda *args, **kwargs: ""
        assert "no file was created" in scope["file_controller"](request)
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


if __name__ == "__main__":
    storage()
    documents()
    asyncio.run(playback())
    print("PASS: native-write cancellation, persistent deletion, offline retries and long replies")
