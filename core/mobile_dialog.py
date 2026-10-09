"""Desktop mobile pairing dialog. Network calls stay off Qt's GUI thread."""
from __future__ import annotations
import io
import threading
import time
from PyQt6.QtCore import QObject, pyqtSignal, Qt, QTimer, QUrl
from PyQt6.QtGui import QPixmap, QDesktopServices
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QListWidget, QInputDialog, QFileDialog, QMessageBox
from core.cloud_bridge import BridgeError, CloudBridge
from core.mobile_bridge import pairing_payload

class _Result(QObject):
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)

class MobileDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ArienX • Connect phone")
        self.setMinimumWidth(360)
        self.setStyleSheet("QDialog{background:#00060a;color:#8ffcff} QLabel{color:#8ffcff} QPushButton{padding:10px;background:#003044;color:#8ffcff} QListWidget{background:#010f18;color:#8ffcff}")
        self.bridge = CloudBridge()
        layout = QVBoxLayout(self)
        self.note = QLabel("Install ArienX Mobile, then tap Scan desktop QR.\nCodes expire after two minutes and work only once.")
        self.note.setWordWrap(True); layout.addWidget(self.note)
        self.qr = QLabel(); self.qr.setFixedSize(280, 280); self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.qr, alignment=Qt.AlignmentFlag.AlignCenter)
        self.generate = QPushButton("GENERATE PAIRING QR"); self.generate.clicked.connect(self.pair); layout.addWidget(self.generate)
        download = QPushButton("DOWNLOAD ANDROID APK"); download.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.bridge.cfg.url + "/download/"))); layout.addWidget(download)
        shared = QPushButton("SHARED CLOUD CONVERSATIONS")
        def open_chats():
            from core.cloud_conversation_dialog import CloudConversationDialog
            CloudConversationDialog(self).exec()
        shared.clicked.connect(open_chats); layout.addWidget(shared)
        upload = QPushButton("SHARE A FILE TO MY PHONES")
        upload.clicked.connect(self.share_file); layout.addWidget(upload)
        self.phones = QListWidget(); layout.addWidget(self.phones)
        refresh = QPushButton("REFRESH TRUSTED PHONES"); refresh.clicked.connect(self.refresh); layout.addWidget(refresh)
        revoke = QPushButton("REVOKE SELECTED PHONE"); revoke.clicked.connect(self.revoke); layout.addWidget(revoke)
        self.result = _Result(self); self.result.ready.connect(self.received); self.result.failed.connect(self.failed)
        self.expiry = 0
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(1000)
        self.finished.connect(lambda _: self.timer.stop())
        self.refresh()

    def run(self, fn):
        self.generate.setEnabled(False)
        def work():
            try: self.result.ready.emit(fn())
            except BridgeError as exc: self.result.failed.emit(str(exc))
            except Exception: self.result.failed.emit("Cloud request failed. Check the connection and desktop pairing.")
        threading.Thread(target=work, daemon=True).start()

    def share_file(self):
        import base64
        from pathlib import Path
        name, _ = QFileDialog.getOpenFileName(self, "Choose a file to upload to your ArienX cloud account")
        if not name: return
        path = Path(name)
        if path.stat().st_size > 3 * 1024 * 1024:
            self.failed("Files must be smaller than 3 MB."); return
        if QMessageBox.question(self, "Upload selected file?", "Upload " + path.name + " to " + self.bridge.cfg.url + "? Do not upload credentials or private keys.") != QMessageBox.StandardButton.Yes: return
        self.run(lambda: {"uploaded": self.bridge.mobile_request("POST", "/api/mobile/files", {"name": path.name, "data": base64.b64encode(path.read_bytes()).decode()})})

    def pair(self):
        self.qr.clear(); self.expiry = 0
        self.run(lambda: {"pair": self.bridge.mobile_request("POST", "/api/mobile/pair", {})})

    def refresh(self):
        self.run(lambda: {"sessions": self.bridge.mobile_request("GET", "/api/mobile/sessions")})

    def revoke(self):
        item = self.phones.currentItem()
        if item:
            session_id = item.data(Qt.ItemDataRole.UserRole)
            self.run(lambda: {"revoked": self.bridge.mobile_request("DELETE", "/api/mobile/sessions", {"id": session_id})})

    def failed(self, message):
        self.generate.setEnabled(True); self.note.setText(message)

    def received(self, result):
        self.generate.setEnabled(True)
        if "pair" in result:
            import qrcode
            value = result["pair"]
            try:
                payload = pairing_payload(value)
                buffer = io.BytesIO(); qrcode.make(payload).save(buffer, format="PNG")
                pixmap = QPixmap(); pixmap.loadFromData(buffer.getvalue())
                self.qr.setPixmap(pixmap.scaled(280, 280, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
                self.expiry = value["expires"] / 1000
            except Exception: self.failed("Could not create QR. Pair Cloud Core first.")
        elif "sessions" in result:
            self.phones.clear()
            for session in result["sessions"].get("sessions", []):
                self.phones.addItem(str(session["label"]) + (" • trusted" if session["active"] else " • revoked"))
                self.phones.item(self.phones.count()-1).setData(Qt.ItemDataRole.UserRole, session["id"])
        elif "revoked" in result: self.refresh()
        elif "uploaded" in result: self.note.setText("File shared. Open Shared files on your phone.")

    def tick(self):
        if self.expiry:
            remaining = max(0, int(self.expiry - time.time()))
            self.note.setText(f"Scan in ArienX Mobile • {remaining}s remaining")
            if not remaining:
                self.qr.clear(); self.qr.setText("Expired — generate a new code"); self.expiry = 0
