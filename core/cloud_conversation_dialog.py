"""Desktop client for the same account-scoped cloud conversations as mobile."""
import json
import threading
import requests
from PyQt6.QtCore import QObject, pyqtSignal, Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QTextEdit, QLineEdit, QPushButton, QLabel
from core.cloud_bridge import CloudBridge, BridgeError

class _Events(QObject):
    result = pyqtSignal(object)
    failed = pyqtSignal(str)

class CloudConversationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ArienX • Shared conversations"); self.resize(680, 620)
        self.bridge = CloudBridge(); self.chat = None; self.busy = False
        layout = QVBoxLayout(self)
        self.note = QLabel("These conversations are shared with your phones. Local desktop chats stay private.")
        self.note.setWordWrap(True); layout.addWidget(self.note)
        self.chats = QListWidget(); self.chats.setMaximumHeight(120); self.chats.itemClicked.connect(self.select); layout.addWidget(self.chats)
        row = QHBoxLayout(); new = QPushButton("NEW SHARED CHAT"); new.clicked.connect(self.create); row.addWidget(new)
        refresh = QPushButton("REFRESH"); refresh.clicked.connect(self.refresh); row.addWidget(refresh); layout.addLayout(row)
        self.transcript = QTextEdit(); self.transcript.setReadOnly(True); layout.addWidget(self.transcript)
        self.input = QLineEdit(); self.input.setPlaceholderText("Message ArienX Cloud Core…"); self.input.returnPressed.connect(self.send); layout.addWidget(self.input)
        self.button = QPushButton("SEND"); self.button.clicked.connect(self.send); layout.addWidget(self.button)
        self.events = _Events(self); self.events.result.connect(self.receive); self.events.failed.connect(self.failure)
        self.refresh()

    def run(self, fn):
        if self.busy: return
        self.busy = True; self.button.setEnabled(False); self.input.setEnabled(False)
        def worker():
            try: self.events.result.emit(fn())
            except Exception: self.events.failed.emit("Cloud request failed. Your input remains here; refresh before retrying.")
        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        self.run(lambda: {"list": self.bridge.mobile_request("GET", "/api/conversations")})
    def create(self):
        self.run(lambda: {"chat": self.bridge.mobile_request("POST", "/api/conversations", {})})
    def select(self, item):
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        self.run(lambda: {"chat": self.bridge.mobile_request("GET", "/api/conversations?id=" + chat_id)})
    def send(self):
        if self.busy or not self.chat or not self.input.text().strip(): return
        chat, message = dict(self.chat), self.input.text().strip()
        def request():
            headers = self.bridge._user_headers()
            if not headers: raise BridgeError("Sign in first")
            response = requests.post(self.bridge.cfg.url + "/api/mobile/chat", headers=headers,
                json={"id": chat["id"], "revision": chat["revision"], "message": message}, stream=True, timeout=(12, 65))
            try:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith(b"data:"): continue
                    event = json.loads(line[5:])
                    if event.get("error"): raise BridgeError("Cloud chat failed")
                    if event.get("done"): return {"sent": event["conversation"]}
                raise BridgeError("Incomplete cloud reply")
            finally: response.close()
        self.run(request)
    def failure(self, message):
        self.busy = False; self.button.setEnabled(True); self.input.setEnabled(True); self.note.setText(message)
    def receive(self, result):
        self.busy = False; self.button.setEnabled(True); self.input.setEnabled(True)
        if "list" in result:
            self.chats.clear()
            for chat in result["list"].get("conversations", []):
                self.chats.addItem(chat["title"]); self.chats.item(self.chats.count()-1).setData(Qt.ItemDataRole.UserRole, chat["id"])
        else:
            self.chat = result.get("chat") or result.get("sent")
            self.transcript.setPlainText("\n\n".join(str(m["role"]).upper() + ":\n" + m["text"] for m in self.chat.get("messages", [])))
            if "sent" in result: self.input.clear()
            self.note.setText("Shared conversation • " + self.chat["title"])
