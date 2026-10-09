"""Gojo GIF companion driven by ArienX's existing runtime events."""
from pathlib import Path
import math
import random
import threading

from PyQt6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QMovie, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QWidget


ANIMATIONS = ("idle", "running", "running-left", "running-right", "waiting", "review", "jumping", "failed", "waving")


class TaskPet(QLabel):
    hide_requested = pyqtSignal()
    mute_requested = pyqtSignal()
    interrupt_requested = pyqtSignal()
    size_requested = pyqtSignal(int)
    _perches_ready = pyqtSignal(object)

    def __init__(self, assets: Path, parent=None):
        # An owned tool window is hidden by the window manager with its parent.
        # Keep a separate desktop window and close it explicitly with ArienX.
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowStaysOnTopHint)
        self._anchor = parent
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent;")
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Gojo task pet • Drag to move • Hover for controls • Right-click for size or hide")
        self.assets = assets
        self.animation = ""
        self._assistant = "INITIALISING"
        self._tasks = set()
        self._jobs = {}
        self._review = False
        self._request = False
        self._transient = ""
        self._drag_offset = None
        self._dragged = False
        self._placed = False
        self._movie = QMovie(self)
        self._movie.setCacheMode(QMovie.CacheMode.CacheNone)
        self._movie.frameChanged.connect(lambda _: self.update())
        self._controls = QWidget(self)
        self._controls.setStyleSheet("QPushButton { background: rgba(0, 15, 24, 225); border: 1px solid #1a5c7a; border-radius: 5px; } QPushButton:hover { background: #003047; } QPushButton:checked { border-color: #ff3355; }")
        row = QHBoxLayout(self._controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._mute_button = QPushButton(self._controls)
        self._mute_button.setCheckable(True)
        self._interrupt_button = QPushButton(self._controls)
        for button in (self._mute_button, self._interrupt_button):
            button.setFixedSize(26, 26)
            button.setIconSize(QSize(18, 18))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        self._interrupt_button.setIcon(self._control_icon(stop=True))
        self._interrupt_button.setToolTip("Interrupt response")
        self._interrupt_button.setAccessibleName("Interrupt response")
        self._mute_button.clicked.connect(self.mute_requested)
        self._interrupt_button.clicked.connect(self.interrupt_requested)
        self._controls.hide()
        self.set_muted(False)
        self.set_scale(100)
        self.setMovie(self._movie)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._settle)
        self._hovered = False
        self._phase = 0.
        self._walk_target = None
        self._walk_direction = 1
        self._perches = []
        self._scan_pending = False
        self._perches_ready.connect(self._accept_perches)
        self._motion = QTimer(self)
        self._motion.setInterval(125)  # eight small repaints/sec; no GIF decoding loop
        self._motion.timeout.connect(self._motion_step)
        self._wander = QTimer(self)
        self._wander.setSingleShot(True)
        self._wander.timeout.connect(self._start_wandering)
        self._roaming = True
        self._play("idle")

    def set_scale(self, percent):
        try:
            percent = max(50, min(200, int(percent)))
        except (TypeError, ValueError):
            percent = 100
        self.scale_percent = percent
        width, height = round(144 * percent / 100), round(156 * percent / 100)
        self.setFixedSize(width, height + 30)
        self._movie.setScaledSize(QSize(width, height))
        self._controls.setGeometry((width - 56) // 2, 2, 56, 26)
        if self._placed:
            self._clamp()

    @staticmethod
    def _control_icon(muted=False, stop=False):
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#ff6688" if muted else "#ffcc00" if stop else "#00d4ff")
        painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if stop:
            painter.setBrush(color)
            painter.drawRoundedRect(4, 4, 10, 10, 1, 1)
        else:
            painter.drawRoundedRect(6, 1, 6, 10, 3, 3)
            painter.drawArc(3, 5, 12, 10, 180 * 16, 180 * 16)
            painter.drawLine(9, 15, 9, 17)
            painter.drawLine(6, 17, 12, 17)
            if muted:
                painter.drawLine(2, 2, 16, 16)
        painter.end()
        return QIcon(pixmap)

    def set_muted(self, muted):
        self._mute_button.setChecked(bool(muted))
        self._mute_button.setIcon(self._control_icon(muted=muted))
        label = "Unmute microphone" if muted else "Mute microphone"
        self._mute_button.setToolTip(label)
        self._mute_button.setAccessibleName(label)

    def enterEvent(self, event):
        self._hovered = True
        self._walk_target = None
        self._sync_pose()
        self._controls.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._schedule_wander()
        self._controls.hide()
        super().leaveEvent(event)

    def _play(self, animation):
        self.animation = animation
        self._sync_pose()

    def _sync_pose(self):
        moving = (self._walk_target is not None and not self._hovered and not self._review
                  or self._drag_offset is not None and self._dragged)
        pose = ("running-left" if self._walk_direction<0 else "running-right") if moving else "idle"
        filename = str(self.assets / f"gojo-satoru-{pose}.gif")
        if self._movie.fileName()!=filename:
            self._movie.stop()
            self._movie.setFileName(filename)
            self._movie.jumpToFrame(0)
        if moving and self.isVisible():
            if self._movie.state()!=QMovie.MovieState.Running:
                self._movie.start()
        else:
            if self._movie.state()!=QMovie.MovieState.NotRunning:
                self._movie.stop()
            if self._movie.currentFrameNumber()!=0:
                self._movie.jumpToFrame(0)
        self.update()

    def paintEvent(self, event):
        pixmap = self._movie.currentPixmap()
        if pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        height = self.height()-30
        phase = math.sin(self._phase)
        lift = 1.5*phase if not self._hovered else 0.
        scale = 1.+.006*phase  # a quiet breath, never a jumping character
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 180, 230, 35))
        painter.drawEllipse(QRectF(self.width()*.27, self.height()-3, self.width()*.46, 3))
        painter.save()
        painter.translate(self.width()/2, 30+height/2+lift)
        painter.scale(scale, scale)
        painter.drawPixmap(QRectF(-self.width()/2, -height/2, self.width(), height),
            pixmap, QRectF(pixmap.rect()))
        painter.restore()
        color = "#ff6688" if self.animation=="failed" else "#ffcc55" if self._review else "#00d4ff"
        painter.setBrush(QColor(color))
        painter.drawEllipse(QRectF(self.width()-10, self.height()-8, 4, 4))
        painter.end()

    def _motion_step(self):
        if not self.isVisible():
            return
        self._phase = (self._phase+.13) % (2*math.pi)
        if self._walk_target is not None and not self._hovered and self._drag_offset is None and not self._review:
            delta = self._walk_target-self.pos()
            distance = math.hypot(delta.x(), delta.y())
            if distance <= 6:
                self.move(self._walk_target)
                self._walk_target = None
                self._schedule_wander()
            else:
                self.move(self.pos()+QPoint(round(delta.x()*6/distance), round(delta.y()*6/distance)))
                self._clamp()
        self._sync_pose()

    def _schedule_wander(self):
        if self.isVisible() and self._roaming and self._walk_target is None:
            self._wander.start(random.randint(60000, 150000))

    def _start_wandering(self):
        if (not self.isVisible() or not self._roaming or self._hovered
                or self._drag_offset is not None or self._review):
            self._schedule_wander()
            return
        bounds = self.screen().availableGeometry()
        bottom = bounds.bottom()-self.height()+1
        # The available desktop edge is above the taskbar/dock.
        x = bounds.left()+12 if self.x()>bounds.center().x() else bounds.right()-self.width()-12
        target = QPoint(x, bottom)
        suitable = [r for r in self._perches if r.top()-self.height() >= bounds.top()
                    and r.width()>=self.width() and bounds.contains(r.center())]
        if suitable and random.random()<.45:
            rect = random.choice(suitable)
            target = QPoint(random.randint(rect.left(), rect.right()-self.width()+1),
                rect.top()-self.height())
        self._walk_target = target
        self._walk_direction = 1 if target.x()>self.x() else -1
        self._sync_pose()
        self._scan_perches()

    def _scan_perches(self):
        # Query geometry only, rarely, off the Qt thread; never focus or move apps.
        if self._scan_pending:
            return
        self._scan_pending = True
        ignored = {int(w.winId()) for w in QApplication.topLevelWidgets()}
        def scan():
            rectangles = []
            display = None
            try:
                from Xlib.display import Display
                display = Display()
                root = display.screen().root
                prop = root.get_full_property(display.intern_atom("_NET_CLIENT_LIST_STACKING"), 0)
                desktop = root.get_full_property(display.intern_atom("_NET_CURRENT_DESKTOP"), 0)
                current = int(desktop.value[0]) if desktop is not None else 0
                for wid in list(prop.value if prop is not None else [])[-20:]:
                    if int(wid) in ignored:
                        continue
                    window = display.create_resource_object("window", int(wid))
                    try:
                        if window.get_attributes().map_state != 2:
                            continue
                        workspace = window.get_full_property(display.intern_atom("_NET_WM_DESKTOP"), 0)
                        if workspace is not None and int(workspace.value[0]) not in (current, 0xffffffff):
                            continue
                        geometry = window.get_geometry()
                        point = root.translate_coords(window, 0, 0)
                        extents = window.get_full_property(display.intern_atom("_NET_FRAME_EXTENTS"), 0)
                        top = int(extents.value[2]) if extents is not None else 0
                        rectangles.append((point.x, point.y-top, geometry.width, geometry.height+top))
                    except Exception:
                        continue
            except Exception:
                pass  # Wayland/no window access: desktop-edge roaming still works.
            finally:
                if display is not None:
                    display.close()
            try:
                self._perches_ready.emit(rectangles)
            except RuntimeError:  # application closed while the geometry scan ran
                pass
        threading.Thread(target=scan, daemon=True, name="pet-window-geometries").start()

    def _accept_perches(self, rectangles):
        from PyQt6.QtCore import QRect
        self._scan_pending = False
        self._perches = [QRect(*row) for row in rectangles]

    def _base(self):
        if self._review:
            return "review"
        statuses = {s for rows in self._jobs.values() for s in rows}
        if statuses & {"planning", "running"}:
            return "running"
        if "verifying" in statuses:
            return "review"
        if statuses & {"queued", "waiting"}:
            return "waiting"
        if self._tasks or self._request or self._assistant in ("THINKING", "PROCESSING"):
            return "running"
        return "waiting" if self._assistant in ("INITIALISING", "SLEEPING", "MUTED") else "idle"

    def _refresh(self):
        if self._drag_offset is None:
            self._play(self._base() if self._review or self._tasks or self._jobs or self._request else self._transient or self._base())

    def _settle(self):
        self._transient = ""
        self._refresh()

    def _pulse(self, animation, milliseconds=2200):
        self._transient = animation
        self._hold.start(milliseconds)
        self._refresh()

    def assistant_state(self, state):
        self._assistant = state
        if state in ("SPEAKING", "LISTENING", "SLEEPING", "MUTED"):
            self._request = False
        self._refresh()

    def task_event(self, event, task_id=""):
        if event == "started":
            self._transient = ""
            self._tasks.add(task_id)
        elif event == "request":
            self._transient = ""
            self._request = True
        elif event == "answered":
            self._request = False
        elif event in ("completed", "failed", "cancelled", "review", "waiting"):
            self._tasks.discard(task_id)
            self._request = False
            if event == "review":
                self._review = True
            elif event in ("completed", "failed"):
                self._pulse("jumping" if event == "completed" else "failed")
            else:
                self._pulse("waiting", 1000)
        elif event in ("waving", "document_review"):
            self._pulse("waving" if event == "waving" else "review")
        self._refresh()

    def review(self, pending):
        self._review = bool(pending)
        self._refresh()

    def workers(self, snapshot):
        job_id = str(snapshot.get("job_id") or "default")
        statuses = {str(row.get("status") or "queued").lower() for row in snapshot.get("workers", [])}
        if not statuses:
            return
        if statuses <= {"completed", "failed", "cancelled"}:
            self._jobs.pop(job_id, None)
            if "failed" in statuses:
                self._pulse("failed")
            elif "cancelled" in statuses:
                self._pulse("waiting", 1000)
            else:
                self._pulse("jumping")
        else:
            self._jobs[job_id] = statuses
        self._refresh()

    def showEvent(self, event):
        if not self._placed:
            parent = self._anchor
            bounds = (parent.screen() if parent else self.screen()).availableGeometry()
            frame = parent.frameGeometry() if parent else bounds
            # Prefer the free desktop beside the app, keeping input/interrupt
            # controls clear. A fullscreen window falls back to the center HUD.
            if frame.right() + self.width() + 12 <= bounds.right():
                x = frame.right() + 12
            elif frame.left() - self.width() - 12 >= bounds.left():
                x = frame.left() - self.width() - 12
            else:
                x = frame.center().x() - self.width() // 2
            self.move(x, frame.bottom() - self.height() - 24)
            self._clamp()
            self._placed = True
        self._motion.start()
        self._schedule_wander()
        super().showEvent(event)

    def hideEvent(self, event):
        self._motion.stop()
        self._wander.stop()
        self._walk_target = None
        self._movie.stop()
        self._controls.hide()
        super().hideEvent(event)

    def _clamp(self):
        screen = QApplication.screenAt(self.frameGeometry().center()) or self.screen()
        bounds = screen.availableGeometry()
        self.move(max(bounds.left(), min(self.x(), bounds.right() - self.width() + 1)),
                  max(bounds.top(), min(self.y(), bounds.bottom() - self.height() + 1)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._walk_target = None
            self._wander.stop()
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self._dragged = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            position = event.globalPosition().toPoint() - self._drag_offset
            if position.x() != self.x():
                self._walk_direction = 1 if position.x()>self.x() else -1
                self._play("running-right" if position.x() > self.x() else "running-left")
            self._dragged = self._dragged or (position - self.pos()).manhattanLength() > 2
            self.move(position)
            self._sync_pose()
            self._clamp()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if not self._dragged:
                self._pulse("waving")
            self._refresh()
            self._schedule_wander()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        sizes = menu.addMenu("Pet size")
        for percent in (50, 75, 100, 125, 150, 200):
            action = sizes.addAction(f"{percent}%")
            action.setCheckable(True)
            action.setChecked(percent == self.scale_percent)
            action.triggered.connect(lambda checked=False, value=percent: self.size_requested.emit(value))
        roam = menu.addAction("Wander around desktop")
        roam.setCheckable(True)
        roam.setChecked(self._roaming)
        def set_roaming(checked):
            self._roaming = checked
            self._walk_target = None
            self._wander.stop()
            if checked:
                self._schedule_wander()
        roam.toggled.connect(set_roaming)
        hide = menu.addAction("Hide Gojo pet")
        if menu.exec(event.globalPos()) is hide:
            self.hide_requested.emit()
