"""Offline Gojo pet checks. Run: QT_QPA_PLATFORM=offscreen python3 -m diagnostics.task_pet_diag"""
import ast
import asyncio
import os
from pathlib import Path
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPointF, Qt, QEvent, QEventLoop, QTimer
from PyQt6.QtGui import QEnterEvent, QMouseEvent, QMovie
from PyQt6.QtWidgets import QApplication
from core.task_pet import ANIMATIONS, TaskPet

ROOT = Path(__file__).resolve().parents[1]


def animations():
    app = QApplication.instance() or QApplication([])
    pet = TaskPet(ROOT / "assets/pets/gojo")
    pet.show()
    assert pet._movie.state() != QMovie.MovieState.Running
    assert pet._motion.interval() == 125 and pet._motion.isActive()
    assert pet.parentWidget() is None
    assert pet.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    pet.set_scale(200); assert pet.width() == 288
    pet.set_scale(50); assert pet.width() == 72 and pet._controls.width() <= pet.width()
    pet.set_scale("bad config"); assert pet.scale_percent == 100
    pet.leaveEvent(QEvent(QEvent.Type.Leave)); assert not pet._controls.isVisible()
    pet.enterEvent(QEnterEvent(QPointF(10,10), QPointF(10,10), QPointF(pet.pos())))
    assert pet._controls.isVisible()
    actions = []
    pet.mute_requested.connect(lambda: actions.append("mute"))
    pet.interrupt_requested.connect(lambda: actions.append("interrupt"))
    pet._mute_button.click(); pet._interrupt_button.click(); assert actions == ["mute", "interrupt"]
    pet.set_muted(True); assert pet._mute_button.isChecked() and not pet._mute_button.text()
    pet.set_muted(False); assert not pet._mute_button.isChecked()
    pet.leaveEvent(QEvent(QEvent.Type.Leave)); assert not pet._controls.isVisible()
    pet.assistant_state("LISTENING")
    for animation in ANIMATIONS:
        movie = QMovie(str(pet.assets / f"gojo-satoru-{animation}.gif"))
        assert movie.isValid(), animation
        assert movie.frameCount() > 1, animation
        assert movie.jumpToFrame(0) and not movie.currentPixmap().isNull(), animation
    pet.task_event("waving"); assert pet.animation == "waving"
    pet.task_event("request"); assert pet.animation == "running"
    pet.task_event("answered"); assert pet.animation == "idle"
    pet.task_event("started", "a"); pet.task_event("started", "b")
    pet.assistant_state("SPEAKING"); assert pet.animation == "running"
    pet.task_event("completed", "a"); assert pet.animation == "running", "one task is still working"
    pet.task_event("completed", "b"); assert pet.animation == "jumping"
    pet._settle(); assert pet.animation == "idle"
    pet.task_event("started", "c"); pet.task_event("failed", "c"); assert pet.animation == "failed"
    pet.task_event("started", "d"); pet.review(True); assert pet.animation == "review"
    pet.task_event("completed", "d"); assert pet.animation == "review", "confirmation still needs the user"
    pet.review(False); pet._settle()
    pet.workers({"job_id": "job", "workers": [{"status": "Queued"}]}); assert pet.animation == "waiting"
    pet.workers({"job_id": "job", "workers": [{"status": "Running"}]}); assert pet.animation == "running"
    pet.workers({"job_id": "job", "workers": [{"status": "Verifying"}]}); assert pet.animation == "review"
    pet.workers({"job_id": "job", "workers": [{"status": "Failed"}]}); assert pet.animation == "failed"
    pet._settle(); pet.assistant_state("MUTED"); assert pet.animation == "waiting"
    pet.assistant_state("LISTENING")
    def mouse(kind, point, button=Qt.MouseButton.NoButton):
        return QMouseEvent(kind, QPointF(20,20), QPointF(point), button, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    start = pet.pos() + QPointF(20,20).toPoint()
    pet.mousePressEvent(mouse(QMouseEvent.Type.MouseButtonPress,start,Qt.MouseButton.LeftButton))
    pet.mouseMoveEvent(mouse(QMouseEvent.Type.MouseMove,start + QPointF(-15,0).toPoint())); assert pet.animation == "running-left"
    pet.mouseMoveEvent(mouse(QMouseEvent.Type.MouseMove,start + QPointF(15,0).toPoint())); assert pet.animation == "running-right"
    pet.mouseReleaseEvent(mouse(QMouseEvent.Type.MouseButtonRelease,start,Qt.MouseButton.LeftButton)); assert pet.animation == "idle"
    pet._pulse("failed", 20)
    loop = QEventLoop(); QTimer.singleShot(80, loop.quit); loop.exec()
    assert pet.animation == "idle", "temporary reactions must return to the current state"
    pet.hide(); assert pet._movie.state() == QMovie.MovieState.NotRunning
    app.processEvents()


async def lifecycle():
    # Exercise the actual dispatcher wrapper, with no tools or providers running.
    tree = ast.parse((ROOT / "main.py").read_text())
    cls = next(n for n in tree.body if getattr(n,"name","") == "JarvisLive")
    method = next(n for n in cls.body if getattr(n,"name","") == "_execute_tool")
    from core.task_state import classify_result
    scope = {"asyncio":asyncio, "types":types.SimpleNamespace(FunctionResponse=object), "classify_result":classify_result}
    exec(compile(ast.Module(body=[method],type_ignores=[]),"main.py","exec"),scope)
    events=[]
    async def implementation(fc): return types.SimpleNamespace(response={"result":"Done."})
    owner=types.SimpleNamespace(ui=types.SimpleNamespace(pet_event=lambda *event:events.append(event)),_execute_tool_impl=implementation)
    tool=types.SimpleNamespace(id="check",name="fake")
    await scope["_execute_tool"](owner,tool)
    assert events == [("started","check"),("completed","check")]
    async def cancel(fc): raise asyncio.CancelledError()
    owner._execute_tool_impl=cancel;events.clear()
    try: await scope["_execute_tool"](owner,tool)
    except asyncio.CancelledError: pass
    else: raise AssertionError("cancellation swallowed")
    assert events == [("started","check"),("cancelled","check")]


if __name__ == "__main__":
    animations()
    asyncio.run(lifecycle())
    print("PASS: nine GIFs, task/worker states, confirmation, completion, failure, drag direction and cancellation")
