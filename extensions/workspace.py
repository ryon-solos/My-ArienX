"""Dedicated Apps workspace; it controls extension processes but never imports them."""
from __future__ import annotations
from PyQt6.QtCore import Qt, QTimer
import ast, json, operator, time
from PyQt6.QtWidgets import QCalendarWidget, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextEdit, QVBoxLayout
from .runtime import get_runtime
from .scaffold import LIBRARY, files_for

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}
def _calculate(text):
    def walk(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS: return _OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS: return _OPS[type(node.op)](walk(node.operand))
        raise ValueError("Use numbers and + - × ÷ only.")
    return walk(ast.parse(text.replace("×", "*").replace("÷", "/"), mode="eval").body)

class ExtensionWindow(QDialog):
    def __init__(self, runtime, app, view, parent=None):
        super().__init__(parent); self.runtime, self.app, self.view = runtime, app, view; self.setWindowTitle(str(view.get("title") or app.name)); self.resize(500, 340)
        self.layout = QVBoxLayout(self); self.layout.addWidget(QLabel(f"{view.get('icon', '◆')}  {view.get('heading') or app.name}")); self.render(str(view.get("kind") or "dashboard"))
    def data(self): return self.runtime.read_storage(self.app.id)
    def save(self, data): self.runtime.write_storage(self.app.id, data)
    def render(self, kind):
        if kind == "calculator":
            display = QLineEdit(); display.setPlaceholderText("Example: (12 + 8) / 2"); result = QLabel("0")
            run = QPushButton("=")
            def calculate():
                try: result.setText(str(_calculate(display.text())) if display.text() else "0")
                except Exception as exc: result.setText(str(exc))
            run.clicked.connect(calculate); self.layout.addWidget(display); self.layout.addWidget(run); self.layout.addWidget(result); return
        if kind == "notes":
            editor = QTextEdit(); editor.setPlainText(str(self.data().get("note", ""))); save = QPushButton("SAVE NOTE"); save.clicked.connect(lambda: self.save({**self.data(), "note": editor.toPlainText()})); self.layout.addWidget(editor); self.layout.addWidget(save); return
        if kind in ("tasks", "calendar"):
            if kind == "calendar": self.layout.addWidget(QCalendarWidget())
            task = QLineEdit(); task.setPlaceholderText("Add a task"); items = QListWidget(); items.addItems(self.data().get("tasks", []))
            add = QPushButton("ADD TASK")
            def add_task():
                if task.text().strip(): items.addItem(task.text().strip()); self.save({**self.data(), "tasks": [items.item(i).text() for i in range(items.count())]}); task.clear()
            add.clicked.connect(add_task); self.layout.addWidget(task); self.layout.addWidget(add); self.layout.addWidget(items); return
        if kind in ("clock", "timer", "pomodoro"):
            label = QLabel("00:00:00"); label.setAlignment(Qt.AlignmentFlag.AlignCenter); label.setStyleSheet("font-size: 34px;"); self.layout.addWidget(label); timer = QTimer(self); state = {"seconds": 25 * 60 if kind == "pomodoro" else 0, "running": kind == "clock"}
            def tick():
                if kind == "clock": label.setText(time.strftime("%H:%M:%S")); return
                if state["running"] and state["seconds"]: state["seconds"] -= 1
                label.setText(f"{state['seconds']//60:02}:{state['seconds']%60:02}")
            timer.timeout.connect(tick); timer.start(1000); tick()
            if kind != "clock":
                toggle = QPushButton("START / PAUSE"); toggle.clicked.connect(lambda: state.update(running=not state["running"])); reset = QPushButton("RESET"); reset.clicked.connect(lambda: state.update(seconds=25*60 if kind == "pomodoro" else 300)); self.layout.addWidget(toggle); self.layout.addWidget(reset)
            return
        if kind == "weather":
            city = QLineEdit(); city.setPlaceholderText("City"); status = QLabel("Choose a city and refresh."); refresh = QPushButton("REFRESH WEATHER")
            def refresh_weather():
                place = city.text().strip() or "your city"; self.save({**self.data(), "city": place}); status.setText(f"Weather dashboard saved for {place}. Browser permission is required for live provider data.")
            refresh.clicked.connect(refresh_weather); self.layout.addWidget(city); self.layout.addWidget(refresh); self.layout.addWidget(status); return
        self.layout.addWidget(QLabel(str(self.view.get("message") or "Ready.")))

class AppsWorkspace(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.runtime = get_runtime(); self.setWindowTitle("ArienX Apps"); self.resize(760, 520)
        layout = QVBoxLayout(self); layout.addWidget(QLabel("APPS WORKSPACE — isolated verified extensions"))
        filters = QHBoxLayout(); self.search = QLineEdit(); self.search.setPlaceholderText("Search apps"); self.category = QComboBox(); self.category.addItem("All"); self.library = QComboBox(); self.library.addItems([name.title() for name in LIBRARY])
        install = QPushButton("INSTALL LIBRARY APP"); install.clicked.connect(self.install_library); filters.addWidget(self.search); filters.addWidget(self.category); filters.addWidget(self.library); filters.addWidget(install); layout.addLayout(filters)
        self.apps = QListWidget(); self.detail = QTextEdit(); self.detail.setReadOnly(True); content = QHBoxLayout(); content.addWidget(self.apps, 2); content.addWidget(self.detail, 3); layout.addLayout(content)
        buttons = QHBoxLayout()
        for label, method in (("OPEN", self.open), ("ENABLE / DISABLE", self.toggle), ("DIAGNOSTICS", self.diagnostics), ("ROLLBACK", self.rollback), ("REMOVE", self.remove)):
            button = QPushButton(label); button.clicked.connect(method); buttons.addWidget(button)
        layout.addLayout(buttons); self.search.textChanged.connect(self.refresh); self.category.currentTextChanged.connect(self.refresh); self.apps.currentItemChanged.connect(self.show_detail); self.refresh()
    def refresh(self):
        selected = self.current_id(); items = self.runtime.list(); categories = sorted({a.category for a in items})
        if [self.category.itemText(i) for i in range(self.category.count())] != ["All", *categories]: self.category.clear(); self.category.addItems(["All", *categories])
        query, category = self.search.text().lower(), self.category.currentText(); self.apps.clear()
        for app in items:
            if query not in (app.name + " " + app.id).lower() or category != "All" and app.category != category: continue
            row = QListWidgetItem(f"{app.name}  ·  {app.category}  ·  {app.version}  ·  {app.status}"); row.setData(Qt.ItemDataRole.UserRole, app.id); self.apps.addItem(row)
            if app.id == selected: self.apps.setCurrentItem(row)
        self.show_detail(self.apps.currentItem())
    def current_id(self):
        item = self.apps.currentItem(); return item.data(Qt.ItemDataRole.UserRole) if item else ""
    def show_detail(self, item, *_):
        if not item: self.detail.setPlainText("No extension selected."); return
        app = self.runtime.info(item.data(Qt.ItemDataRole.UserRole)); self.detail.setPlainText(f"{app.name}\nID: {app.id}\nVersion: {app.version}\nCategory: {app.category}\nEnabled: {app.enabled}\nStatus: {app.status}\n\nDiagnostics:\n" + "\n".join(app.diagnostics or ["No diagnostics."]))
    def open(self):
        try:
            app = self.runtime.info(self.current_id()); self.runtime.launch(app.id)
            manifest = json.loads((app.root / "manifest.json").read_text(encoding="utf-8")); view = manifest.get("ui", {})
            window = ExtensionWindow(self.runtime, app, view, self)
            window.show(); window.raise_(); self._app_windows = getattr(self, "_app_windows", []); self._app_windows.append(window); self.refresh()
        except Exception as exc: QMessageBox.warning(self, "Apps", str(exc))
    def install_library(self):
        try:
            key = self.library.currentText().lower(); app_id, files = files_for(f"Install {key} app", key.title()); staged = self.runtime.stage(app_id, files); self.runtime.install(staged); self.refresh()
        except Exception as exc: QMessageBox.warning(self, "Apps", str(exc))
    def toggle(self):
        try:
            app = self.runtime.info(self.current_id()); self.runtime.set_enabled(app.id, not app.enabled); self.refresh()
        except Exception as exc: QMessageBox.warning(self, "Apps", str(exc))
    def diagnostics(self): self.show_detail(self.apps.currentItem())
    def rollback(self):
        try: self.runtime.rollback(self.current_id()); self.refresh()
        except Exception as exc: QMessageBox.warning(self, "Apps", str(exc))
    def remove(self):
        app_id = self.current_id()
        if app_id and QMessageBox.question(self, "Remove app", f"Remove {app_id} and its isolated storage?") == QMessageBox.StandardButton.Yes: self.runtime.uninstall(app_id); self.refresh()
