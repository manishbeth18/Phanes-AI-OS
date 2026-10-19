"""Phanes AI-OS: desktop UI prototype ("Nocturne Opal").

Run:    pip install PySide6   then   python main.py
Keys:   Ctrl+K command palette   Ctrl+Enter run   Ctrl+Shift+A activity drawer   Ctrl+N new task

WHAT THIS IS
    A single-file, runnable visual front end. It needs only PySide6.
    Everything behind the UI (planner, executor, verifier) is a SIMULATED
    service, clearly labelled in the interface. It never touches your files,
    never runs commands, and never claims real work happened.

HOW IT STAYS HONEST (architecture rules)
    * Task status changes only when the service sends a status event.
    * A step's outcome comes only from a result event, and "completed" is kept
      separate from "verified": Verified success / Completed - unverified /
      Verified failure / Failed - unverified / Unknown outcome.
    * Approval is requested through the service; the UI is never the only gate.
    * The UI has no shell, no SQL. The backend is injected (TaskService).

    
LATER: split into ui/ modules, swap SimulatedTaskService for the real
orchestrator, and map the event dataclasses onto shared/schemas.py models.
Status strings below intentionally match the architecture document's enums.
"""

from __future__ import annotations

import math
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from shared.service import (
    TaskService,
    TaskEvent,
    StepStartedEvent,
    StepFinishedEvent,
    LogEvent,
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    TaskFinishedEvent,
)
from shared.contracts import ApprovalDecision, TaskStatus as SharedTaskStatus

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QObject,
    QPointF,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QCursor,
    QFont,
    QFontMetrics,
    QGradient,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# =============================================================================
# 1. DESIGN TOKENS: "Nocturne Opal"
# =============================================================================
INK = "#06080D"
TEXT = "#ECEFF6"
MUTED = "#8A93A8"
FAINT = "#566076"
MINT = "#7CF0D0"  # primary: opal mint
IRIS = "#8FA6FF"  # periwinkle
ORCHID = "#D9A2FF"  # orchid
AMBER = "#F4C877"
CORAL = "#FF7E8E"

TONES = {
    "success": MINT,
    "info": IRIS,
    "warning": AMBER,
    "danger": CORAL,
    "unknown": ORCHID,
    "muted": MUTED,
    "neutral": "#B7C0D4",
}
FONT_FAMILIES = ["Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Arial"]
SERIF_FAMILIES = ["Georgia", "Cambria", "Palatino Linotype", "Times New Roman"]
MONO_FAMILIES = ["Cascadia Mono", "Cascadia Code", "Consolas", "Menlo", "Courier New"]


def qc(hex_color: str, alpha: float = 1.0) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


def css(hex_color: str, alpha: float = 1.0) -> str:
    """CSS rgba() string for use inside Qt style sheets."""
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def mix(a: str, b: str, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t),
    )


def make_font(
    px: int,
    weight: QFont.Weight = QFont.Weight.Normal,
    mono: bool = False,
    serif: bool = False,
    italic: bool = False,
) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES if mono else SERIF_FAMILIES if serif else FONT_FAMILIES)
    font.setPixelSize(px)
    font.setWeight(weight)
    font.setItalic(italic)
    return font


FACES = {
    "sans": "",
    "serif": "font-family:'Georgia','Cambria','Times New Roman';",
    "mono": "font-family:'Cascadia Mono','Consolas','Courier New';",
}


def label(
    text: str = "",
    px: int = 13,
    color: str = TEXT,
    weight: str = "400",
    wrap: bool = False,
    face: str = "sans",
    italic: bool = False,
) -> QLabel:
    """Styled label. ``face`` mixes typefaces: sans for UI, serif for voice, mono for data/labels."""
    widget = QLabel(text)
    extra = FACES[face] + ("font-style:italic;" if italic else "")
    widget.setStyleSheet(f"color:{color}; font-size:{px}px; font-weight:{weight}; background:transparent; {extra}")
    widget.setWordWrap(wrap)
    return widget


def em(text: str, color: str | None = None) -> str:
    """Inline serif-italic emphasis for rich-text labels."""
    style = "font-family:'Georgia'; font-style:italic;" + (f" color:{color};" if color else "")
    return f'<i style="{style}">{text}</i>'


def tag(text: str, color: str = MUTED, px: int = 11) -> QLabel:
    """Small mono caption, upper-cased."""
    return label(text.upper(), px, color, "700", face="mono")

# --- icons: inline SVG line icons (24x24 grid), rendered through QtSvg ---------
ICONS = {
    "check": '<polyline points="5 12.5 10 17.5 19 7"/>',
    "x": '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
    "alert": '<path d="M12 3.8 L21.2 19.5 H2.8 Z"/><line x1="12" y1="10" x2="12" y2="14"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "search": '<circle cx="11" cy="11" r="6"/><line x1="15.5" y1="15.5" x2="20" y2="20"/>',
    "terminal": '<polyline points="5 8 9.5 12 5 16"/><line x1="12" y1="16.5" x2="18.5" y2="16.5"/>',
    "layers": '<polygon points="12 4 21 9 12 14 3 9"/><polyline points="3 13.5 12 18.5 21 13.5"/>',
    "folder": '<path d="M3 7.5 A2 2 0 0 1 5 5.5 H9.5 L11.5 8 H19 A2 2 0 0 1 21 10 V17.5 A2 2 0 0 1 19 19.5 H5 A2 2 0 0 1 3 17.5 Z"/>',
    "history": '<path d="M4 12 A8 8 0 1 0 6.3 6.3"/><polyline points="4 4.5 4 9 8.5 9"/><polyline points="12 8 12 12 15 13.5"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "shield": '<path d="M12 3.5 L19.5 6.5 V12 C19.5 16.5 16.2 19.5 12 20.5 C7.8 19.5 4.5 16.5 4.5 12 V6.5 Z"/><polyline points="8.8 12 11 14.2 15.2 9.8"/>',
    "arrow-left": '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="11 6 5 12 11 18"/>',
    "arrow-right": '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="13 6 19 12 13 18"/>',
    "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    "pulse": '<polyline points="3 12 7 12 10 5 14 19 17 12 21 12"/>',
}


def svg_pixmap(name: str, color: str, size: int = 18, stroke: float = 1.8) -> QPixmap:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>'
    )
    dpr = 2.0
    pixmap = QPixmap(int(size * dpr), int(size * dpr))
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def icon_label(name: str, color: str = MUTED, size: int = 18) -> QLabel:
    widget = QLabel()
    widget.setPixmap(svg_pixmap(name, color, size))
    widget.setFixedSize(size, size)
    widget.setStyleSheet("background:transparent;")
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return widget


def opal_gradient(x0: float, y0: float, x1: float, y1: float, alpha: float = 1.0) -> QLinearGradient:
    gradient = QLinearGradient(x0, y0, x1, y1)
    gradient.setColorAt(0.0, qc(MINT, alpha))
    gradient.setColorAt(0.55, qc(IRIS, alpha))
    gradient.setColorAt(1.0, qc(ORCHID, alpha))
    return gradient


# =============================================================================
# 2. ANIMATION PRIMITIVES
# =============================================================================
class Tween(QObject):
    """Animates a float toward a target. Widgets read ``.value`` while painting."""

    changed = Signal(float)
    finished = Signal()

    def __init__(self, parent: QObject, value: float = 0.0, curve: QEasingCurve.Type = QEasingCurve.Type.OutCubic):
        super().__init__(parent)
        self.value = float(value)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(curve)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self.finished)

    def _on_value(self, v) -> None:
        self.value = float(v)
        self.changed.emit(self.value)

    def to(self, target: float, ms: int = 220) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.value)
        self._anim.setEndValue(float(target))
        self._anim.setDuration(ms)
        self._anim.start()

    def jump(self, value: float) -> None:
        self._anim.stop()
        self.value = float(value)
        self.changed.emit(self.value)


class Loop(QObject):
    """Endless 0..1 ramp (spinners, sweeps, rotating borders)."""

    changed = Signal(float)

    def __init__(self, parent: QObject, ms: int):
        super().__init__(parent)
        self.value = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(ms)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._anim.valueChanged.connect(self._on_value)

    def _on_value(self, v) -> None:
        self.value = float(v)
        self.changed.emit(self.value)

    def start(self) -> None:
        if self._anim.state() != QVariantAnimation.State.Running:
            self._anim.start()

    def stop(self) -> None:
        self._anim.stop()

    @property
    def running(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running


def fade_in(widget: QWidget, delay: int = 0, ms: int = 700) -> QPropertyAnimation:
    """Fade a widget in once, then drop the effect so it costs nothing afterwards."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def done() -> None:
        try:
            widget.setGraphicsEffect(None)
        except RuntimeError:  # widget already destroyed
            pass

    anim.finished.connect(done)
    QTimer.singleShot(delay, anim.start)
    return anim


# =============================================================================
# 3. DOMAIN: events, view models, services
# =============================================================================
STATUS_LABEL = {
    "pending": ("Pending", "muted"),
    "planning": ("Planning", "info"),
    "running": ("Running", "info"),
    "waiting_for_approval": ("Waiting for approval", "warning"),
    "completed": ("Completed", "neutral"),  # deliberately not green: "ended", not "worked"
    "failed": ("Failed", "danger"),
    "cancelled": ("Cancelled", "muted"),
    "unknown": ("Unknown outcome", "unknown"),
    "skipped": ("Skipped", "muted"),
}
TERMINAL = {"completed", "failed", "cancelled"}
STAGES = ["Understand context", "Create a plan", "Execute actions", "Verify results"]


@dataclass
class ResultVM:
    status: str
    success: bool
    verified: bool
    details: str | None = None
    output: str = ""


@dataclass
class StepVM:
    step_id: str
    title: str
    tool: str
    stage: int
    requires_approval: bool = False
    status: str = "pending"
    message: str | None = None
    result: ResultVM | None = None


def classify(result: ResultVM | None) -> tuple[str, str]:
    """(label, tone). Never upgrades: success needs success AND verified."""
    if result is None:
        return "No result reported", "muted"
    if result.status == "unknown":
        return "Unknown outcome", "unknown"
    if result.status == "cancelled":
        return "Cancelled", "muted"
    if result.success and result.status == "completed":
        return ("Verified success", "success") if result.verified else ("Completed · unverified", "warning")
    if not result.success and result.status != "completed":
        return ("Verified failure", "danger") if result.verified else ("Failed · unverified", "danger")
    return "Unknown outcome", "unknown"  # contradictory fields


@dataclass
class TaskStatusEv:
    status: str
    message: str | None = None


@dataclass
class PlanEv:
    steps: list[StepVM]


@dataclass
class StepStatusEv:
    step_id: str
    status: str
    message: str | None = None


@dataclass
class ResultEv:
    step_id: str
    result: ResultVM


@dataclass
class ApprovalEv:
    approval_id: str
    step_id: str
    tool: str
    summary: str
    arguments: dict = field(default_factory=dict)


@dataclass
class LogEv:
    text: str
    level: str = "info"





WAIT = object()


class SimulatedTaskService(QObject):
    """Scripted stand-in for planner + executor. Does NO real work."""

    event = Signal(object)
    def subscribe(self, callback: Callable[[object], None]) -> Callable[[], None]:
        """Subscribe to service events and return an unsubscribe function."""
        self.event.connect(callback)

        def unsubscribe() -> None:
            try:
                self.event.disconnect(callback)
            except (RuntimeError, TypeError):
                pass

        return unsubscribe


    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._gen = None
        self._cancel = False
        self._approved: bool | None = None
        self._task_id = ""

    # -- TaskService ----------------------------------------------------------
    def submit(self, goal: str, workspace: str) -> str:
        if "offline" in goal.lower():
            raise RuntimeError("The simulated backend is offline (you included the word 'offline' to test this state).")
        self._cancel, self._approved = False, None
        self._task_id = f"sim-{int(time.time())}"
        self._gen = self._script(goal, workspace)
        self._timer.start(250)
        return self._task_id

    def cancel(self, task_id: str) -> None:
        if task_id != self._task_id:
            return
        self._cancel = True
        self._timer.start(250)

    def resolve_approval(self, decision: ApprovalDecision) -> None:
        if decision.task_id != self._task_id:
            return
        self._approved = decision.approved
        self._timer.start(400)

    def get_task_status(self, task_id: str) -> SharedTaskStatus:
        if task_id != self._task_id:
            return SharedTaskStatus.CREATED
        return getattr(self, "_shared_status", SharedTaskStatus.CREATED)

    # -- driver ---------------------------------------------------------------
    def _tick(self) -> None:
        if self._gen is None:
            return
        if self._cancel:
            self._finish_cancel("You asked to cancel.")
            return
        try:
            delay = next(self._gen)
        except StopIteration:
            self._gen = None
            return
        if delay is not WAIT:
            self._timer.start(int(delay))

    def _finish_cancel(self, message: str) -> None:
        self._gen = None
        self.event.emit(LogEv("(simulated) Cancellation acknowledged.", "warning"))
        self.event.emit(TaskStatusEv("cancelled", message))

    def _script(self, goal: str, workspace: str):
        emit = self.event.emit
        will_fail = "fail" in goal.lower()
        steps = [
            StepVM("s1", "Collect project context", "context.retrieve", 0),
            StepVM("s2", "Draft the plan", "planner.create_plan", 1),
            StepVM("s3", "Apply the proposed change", "fs.edit", 2, requires_approval=True),
            StepVM("s4", "Run the test suite", "terminal.pytest", 2),
            StepVM("s5", "Check the outcome", "verify.tests", 3),
        ]
        emit(TaskStatusEv("planning"))
        emit(LogEv("(simulated) Reading the goal and the selected workspace."))
        yield 900
        emit(PlanEv(steps))
        emit(TaskStatusEv("running"))

        emit(StepStatusEv("s1", "running"))
        emit(LogEv(f"(simulated) Scanning {Path(workspace).name or workspace} for relevant files…", "info"))
        yield 1300
        emit(ResultEv("s1", ResultVM("completed", True, True, "Checked: 12 candidate files were read (simulated).")))

        emit(StepStatusEv("s2", "running"))
        emit(LogEv("(simulated) Drafting a 5-step plan.", "info"))
        yield 1200
        emit(ResultEv("s2", ResultVM("completed", True, False, "No independent check is performed on plans.")))

        emit(StepStatusEv("s3", "waiting_for_approval", "Needs your approval"))
        emit(TaskStatusEv("waiting_for_approval", "A file edit needs your approval"))
        emit(
            ApprovalEv(
                "ap-1",
                "s3",
                "fs.edit",
                "Edit src/parser.py: change one function to handle empty input.",
                {"path": "src/parser.py", "operation": "replace_range", "lines": "42-57", "token": "hidden-demo"},
            )
        )
        yield WAIT
        if self._cancel:
            self._finish_cancel("You asked to cancel.")
            return
        if not self._approved:
            emit(StepStatusEv("s3", "cancelled", "Rejected by you"))
            for sid in ("s4", "s5"):
                emit(StepStatusEv(sid, "cancelled", "Not run: the edit was rejected"))
            emit(TaskStatusEv("cancelled", "You rejected the proposed edit; nothing was changed."))
            return
        emit(TaskStatusEv("running"))
        emit(StepStatusEv("s3", "running"))
        yield 1300
        emit(ResultEv("s3", ResultVM("completed", True, True, "Checked: file hash changed as expected (simulated).")))

        emit(StepStatusEv("s4", "running"))
        for line in ("collected 18 items", "tests/test_parser.py ........", "tests/test_io.py .........."):
            emit(LogEv(f"(simulated) {line}", "stdout"))
            yield 450
        if will_fail:
            emit(LogEv("(simulated) 2 failed, 16 passed", "stderr"))
            emit(ResultEv("s4", ResultVM("failed", False, True, "Checked: exit code 1 (simulated).", "2 failed")))
        else:
            emit(ResultEv("s4", ResultVM("completed", True, True, "Checked: pytest exit code 0 (simulated).")))

        emit(StepStatusEv("s5", "running"))
        yield 900
        if will_fail:
            emit(ResultEv("s5", ResultVM("failed", False, False, "Could not confirm a fix.")))
            emit(TaskStatusEv("failed", "Tests still fail after the change (simulated)."))
        else:
            emit(ResultEv("s5", ResultVM("completed", True, True, "Checked: all 18 tests passed (simulated).")))
            emit(TaskStatusEv("completed"))


# =============================================================================
# 4. VISUAL WIDGETS
# =============================================================================
class Aurora(QWidget):
    """Drifting opal glow. Follows the cursor slightly and re-tints for each intent mode."""

    DEFAULT = (MINT, IRIS, ORCHID)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._t0 = time.monotonic()
        self._from = self._to = self.DEFAULT
        self._tint = Tween(self, 1.0, QEasingCurve.Type.InOutCubic)
        self._px = self._py = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)  # ~25 fps: smooth enough for slow drift, cheap on the CPU
        self._timer.timeout.connect(self.update)
        self._timer.start()

    def set_trip(self, trip) -> None:
        self._from = self._current()
        self._to = tuple(trip)
        self._tint.jump(0.0)
        self._tint.to(1.0, 1100)

    def _current(self) -> tuple[str, str, str]:
        t = self._tint.value
        return tuple(mix(a, b, t).name() for a, b in zip(self._from, self._to))  # type: ignore[return-value]

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(INK))
        t = (time.monotonic() - self._t0) / 18.0
        w, h = self.width(), self.height()
        cursor = self.mapFromGlobal(QCursor.pos())
        self._px += ((cursor.x() / max(w, 1) - 0.5) - self._px) * 0.05
        self._py += ((cursor.y() / max(h, 1) - 0.5) - self._py) * 0.05
        c1, c2, c3 = self._current()
        blobs = [
            (0.12 + 0.07 * math.sin(t) - self._px * 0.07, 0.08 + 0.06 * math.cos(t * 1.3) - self._py * 0.07, 0.62, c1, 0.12),
            (0.88 + 0.06 * math.cos(t * 0.8) - self._px * 0.10, 0.22 + 0.08 * math.sin(t * 0.7) - self._py * 0.10, 0.58, c2, 0.125),
            (0.55 + 0.10 * math.sin(t * 0.5) - self._px * 0.05, 1.02 + 0.04 * math.cos(t), 0.66, c3, 0.09),
        ]
        for fx, fy, radius, color, alpha in blobs:
            gradient = QRadialGradient(QPointF(w * fx, h * fy), max(w, h) * radius)
            gradient.setColorAt(0.0, qc(color, alpha))
            gradient.setColorAt(1.0, qc(color, 0.0))
            p.fillRect(self.rect(), QBrush(gradient))


class Glass(QFrame):
    """Translucent rounded panel. When hoverable it brightens and a soft spotlight follows the cursor."""

    def __init__(self, parent: QWidget | None = None, radius: int = 18, hoverable: bool = False, tint: float = 0.04):
        super().__init__(parent)
        self.radius, self.hoverable, self.tint = radius, hoverable, tint
        self.accent = MINT
        self.tone_mix = 0.0  # constant border tint (e.g. warning banners)
        self._mouse = QPointF(-1, -1)
        self.hover = Tween(self)
        self.hover.changed.connect(lambda _v: self.update())
        self._spot = QTimer(self)
        self._spot.setInterval(33)
        self._spot.timeout.connect(self._track)

    def _track(self) -> None:
        local = self.mapFromGlobal(QCursor.pos())
        inside = self.rect().contains(local)
        if not inside and self.hover.value < 0.01:
            self._spot.stop()
            return
        self._mouse = QPointF(local)
        self.update()

    def enterEvent(self, event) -> None:
        if self.hoverable:
            self.hover.to(1.0, 180)
            self._spot.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # Moving onto a child widget also fires leave; ignore that case.
        if self.hoverable and not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.hover.to(0.0, 280)
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        h = self.hover.value
        p.setBrush(QColor(255, 255, 255, int(255 * (self.tint + 0.04 * h))))
        edge = mix("#262D3B", self.accent, min(1.0, self.tone_mix + 0.55 * h))
        edge.setAlphaF(0.55 + 0.45 * h)
        p.setPen(QPen(edge, 1))
        p.drawRoundedRect(rect, self.radius, self.radius)
        if self.hoverable and h > 0.02 and self.rect().contains(self._mouse.toPoint()):
            clip = QPainterPath()
            clip.addRoundedRect(rect, self.radius, self.radius)
            p.setClipPath(clip)
            spot = QRadialGradient(self._mouse, max(self.width(), self.height()) * 0.6)
            spot.setColorAt(0.0, qc(self.accent, 0.17 * h))
            spot.setColorAt(1.0, qc(self.accent, 0.0))
            p.fillRect(self.rect(), QBrush(spot))




class PulseDot(QWidget):
    def __init__(self, color: str = MINT, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._color = color
        self._loop = Loop(self, 1700)
        self._loop.changed.connect(lambda _v: self.update())
        self._loop.start()

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        v = self._loop.value
        p.setPen(QPen(qc(self._color, 0.55 * (1 - v)), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, 3.5 + 5.5 * v, 3.5 + 5.5 * v)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(self._color))
        p.drawEllipse(c, 3.2, 3.2)


class Pill(QWidget):
    """Compact status pill (translucent, tone-coloured)."""

    def __init__(self, text: str = "", tone: str = "muted", dot: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._font = make_font(11, QFont.Weight.DemiBold)
        self._dot = dot
        self._text, self._tone = text, tone
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set(text, tone)

    def set(self, text: str, tone: str) -> None:
        self._text, self._tone = text, tone
        self.setFixedSize(self.sizeHint())
        self.update()

    def text(self) -> str:
        return self._text

    def tone(self) -> str:
        return self._tone

    def sizeHint(self) -> QSize:
        width = QFontMetrics(self._font).horizontalAdvance(self._text) + 22 + (12 if self._dot else 0)
        return QSize(width, 24)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = TONES.get(self._tone, MUTED)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(qc(color, 0.11))
        p.setPen(QPen(qc(color, 0.34), 1))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        x = 11.0
        if self._dot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qc(color))
            p.drawEllipse(QPointF(x, rect.height() / 2 + 0.5), 2.6, 2.6)
            x += 9
        p.setPen(qc(color))
        p.setFont(self._font)
        p.drawText(QRectF(x, 0, self.width() - x, self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)


class StepNode(QWidget):
    """Status glyph: rotating arc while running, pop-in animation on change."""

    COLORS = {
        "pending": FAINT,
        "running": MINT,
        "done": MINT,
        "unverified": AMBER,
        "failed": CORAL,
        "waiting": AMBER,
        "cancelled": MUTED,
        "unknown": ORCHID,
    }

    def __init__(self, size: int = 28, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._state = "pending"
        self._spin = Loop(self, 1000)
        self._spin.changed.connect(lambda _v: self.update())
        self._pop = Tween(self, 1.0, QEasingCurve.Type.OutBack)
        self._pop.changed.connect(lambda _v: self.update())

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        if state == "running":
            self._spin.start()
        else:
            self._spin.stop()
        self._pop.jump(0.0)
        self._pop.to(1.0, 450)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        c = QPointF(s / 2, s / 2)
        r = (s / 2 - 2.5) * (0.55 + 0.45 * self._pop.value)
        color = self.COLORS[self._state]
        state = self._state
        p.setBrush(Qt.BrushStyle.NoBrush)
        if state in ("done", "unverified", "failed", "unknown", "waiting"):
            p.setBrush(qc(color, 0.16))
        p.setPen(QPen(qc(color, 0.9 if state != "pending" else 0.55), 1.6))
        p.drawEllipse(c, r, r)
        pen = QPen(qc(color), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if state == "running":
            rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            p.setPen(QPen(qc(MINT), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, int(-self._spin.value * 360 * 16), 110 * 16)
        elif state in ("done", "unverified"):
            p.drawPolyline([QPointF(c.x() - 0.42 * r, c.y() + 0.04 * r), QPointF(c.x() - 0.1 * r, c.y() + 0.34 * r), QPointF(c.x() + 0.44 * r, c.y() - 0.3 * r)])
        elif state == "failed":
            d = 0.34 * r
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))
        elif state == "waiting":
            p.drawLine(QPointF(c.x() - 0.2 * r, c.y() - 0.32 * r), QPointF(c.x() - 0.2 * r, c.y() + 0.32 * r))
            p.drawLine(QPointF(c.x() + 0.2 * r, c.y() - 0.32 * r), QPointF(c.x() + 0.2 * r, c.y() + 0.32 * r))
        elif state == "cancelled":
            p.drawLine(QPointF(c.x() - 0.34 * r, c.y()), QPointF(c.x() + 0.34 * r, c.y()))
        elif state == "unknown":
            p.setFont(make_font(max(int(s * 0.5), 9), QFont.Weight.Bold))
            p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "?")


def node_state_for(step: StepVM) -> str:
    """Map a step to a glyph. Without a backend result we never show a green check."""
    if step.status == "running":
        return "running"
    if step.status == "waiting_for_approval":
        return "waiting"
    if step.status in ("cancelled", "skipped"):
        return "cancelled"
    if step.status == "unknown":
        return "unknown"
    if step.status == "failed":
        return "failed"
    if step.status == "completed":
        r = step.result
        if r is None:
            return "unverified"
        if r.success and r.verified:
            return "done"
        return "unverified" if r.success else "failed"
    return "pending"


class GlowBar(QWidget):
    """Progress bar with eased value changes and a light sweep while busy."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._value = Tween(self)
        self._value.changed.connect(lambda _v: self.update())
        self._sweep = Loop(self, 1500)
        self._sweep.changed.connect(lambda _v: self.update())
        self._busy = False
        self._indeterminate = False

    def set_progress(self, fraction: float, busy: bool, indeterminate: bool = False) -> None:
        self._value.to(max(0.0, min(1.0, fraction)), 600)
        self._busy, self._indeterminate = busy, indeterminate
        if busy:
            self._sweep.start()
        else:
            self._sweep.stop()
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.fillPath(track, qc("#FFFFFF", 0.07))
        p.setClipPath(track)
        if self._indeterminate:
            x = (self._sweep.value * 1.4 - 0.3) * w
            p.fillRect(QRectF(x, 0, w * 0.3, h), QBrush(opal_gradient(x, 0, x + w * 0.3, 0)))
            return
        fill_w = w * self._value.value
        if fill_w > 0:
            p.fillRect(QRectF(0, 0, fill_w, h), QBrush(opal_gradient(0, 0, max(w, 1), 0)))
            if self._busy:
                x = (self._sweep.value * 1.5 - 0.25) * fill_w
                band = QLinearGradient(x, 0, x + 70, 0)
                band.setColorAt(0.0, qc("#FFFFFF", 0.0))
                band.setColorAt(0.5, qc("#FFFFFF", 0.35))
                band.setColorAt(1.0, qc("#FFFFFF", 0.0))
                p.fillRect(QRectF(0, 0, fill_w, h), QBrush(band))


class GradientButton(QPushButton):
    """Primary action: opal gradient pill with an eased hover glow."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self.setFont(make_font(14, QFont.Weight.DemiBold))
        self._arrow = svg_pixmap("arrow-right", INK, 16, 2.2)
        self.hover = Tween(self)
        self.hover.changed.connect(lambda _v: self.update())

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self.text()) + 62, 42)

    def enterEvent(self, event) -> None:
        self.hover.to(1.0, 200)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.hover.to(0.0, 300)
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.hover.value
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.isDown():
            rect = rect.adjusted(1, 1, -1, -1)
        radius = rect.height() / 2
        if not self.isEnabled():
            p.setBrush(qc("#FFFFFF", 0.06))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, radius, radius)
            p.setPen(qc(FAINT))
            p.setFont(self.font())
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
            return
        if h > 0.01:  # glow halo
            for grow, alpha in ((6, 0.05), (4, 0.07), (2, 0.09)):
                halo = rect.adjusted(-grow, -grow, grow, grow)
                p.setBrush(qc(MINT, alpha * h))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(halo, halo.height() / 2, halo.height() / 2)
        shift = 90 * h
        p.setBrush(QBrush(opal_gradient(rect.left() - shift, 0, rect.right() + shift, 0)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, radius, radius)
        p.setPen(QColor(INK))
        p.setFont(self.font())
        p.drawText(rect.adjusted(0, 0, -14, 0), Qt.AlignmentFlag.AlignCenter, self.text())
        p.drawPixmap(int(rect.right() - 30 + 3 * h), int(rect.center().y() - 8), self._arrow)


class GhostButton(QPushButton):
    def __init__(self, text: str, tone: str = "neutral", icon_name: str | None = None, parent: QWidget | None = None):
        super().__init__(text, parent)
        color = TONES.get(tone, MUTED)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            self.setIcon(QIcon(svg_pixmap(icon_name, color, 16)))
            self.setIconSize(QSize(16, 16))
        self.setStyleSheet(
            f"QPushButton{{background:{css('#FFFFFF', 0.04)};color:{color};"
            f"border:1px solid {css(color, 0.35)};border-radius:10px;padding:8px 14px;font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{background:{css(color, 0.12)};}}"
            f"QPushButton:disabled{{color:{FAINT};border-color:{css('#FFFFFF', 0.08)};background:transparent;}}"
        )


class Elided(QLabel):
    def __init__(self, text: str = "", px: int = 12, color: str = MUTED, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"color:{color}; font-size:{px}px; background:transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full = ""
        self.set_full(text)

    def set_full(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = QFontMetrics(self.font())
        self.setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(self.width(), 30)))


class LogoMark(QWidget):
    """Brand mark: a tile with a slowly rotating opal ring around a 'P'."""

    def __init__(self, size: int = 40, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._loop = Loop(self, 8000)
        self._loop.changed.connect(lambda _v: self.update())
        self._loop.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = float(self.width())
        rect = QRectF(1.5, 1.5, s - 3, s - 3)
        p.setBrush(qc("#0E1320"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, s * 0.3, s * 0.3)
        ring = QConicalGradient(QPointF(s / 2, s / 2), -self._loop.value * 360)
        ring.setColorAt(0.0, qc(MINT))
        ring.setColorAt(0.4, qc(IRIS))
        ring.setColorAt(0.75, qc(ORCHID))
        ring.setColorAt(1.0, qc(MINT))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(ring), 1.8))
        p.drawRoundedRect(rect, s * 0.3, s * 0.3)
        glyph = QPainterPath()
        glyph.moveTo(s * 0.38, s * 0.72)
        glyph.lineTo(s * 0.38, s * 0.28)
        glyph.lineTo(s * 0.54, s * 0.28)
        glyph.arcTo(QRectF(s * 0.40, s * 0.28, s * 0.28, s * 0.26), 90, -180)
        glyph.lineTo(s * 0.38, s * 0.54)
        pen = QPen(QBrush(opal_gradient(0, 0, s, s)), 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(glyph)


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(qc("#0E1320"))
    painter.setPen(QPen(QBrush(opal_gradient(0, 0, 64, 64)), 3))
    painter.drawRoundedRect(QRectF(3, 3, 58, 58), 18, 18)
    painter.setFont(make_font(36, QFont.Weight.Bold))
    painter.setPen(qc(MINT))
    painter.drawText(QRectF(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "P")
    painter.end()
    return QIcon(pixmap)


# =============================================================================
# 4b. TYPOGRAPHY, INTENT MODES, SCROLLING, COMMAND PALETTE
# =============================================================================
MODES = [  # (name, aurora trio, prompt prefix)
    ("Investigate", (MINT, IRIS, ORCHID), "Investigate: "),
    ("Fix", (AMBER, CORAL, ORCHID), "Fix: "),
    ("Build", (IRIS, ORCHID, MINT), "Build: "),
    ("Review", (MINT, AMBER, IRIS), "Review: "),
]
MODE_SUGGESTIONS = [
    ["Map this codebase and explain how it fits together…", "Trace why the login request is slow…", "Find where configuration is loaded…"],
    ["Find why the tests are failing and fix it…", "Fix the crash when the input file is empty…", "Fix the flaky timeout in the sync job…"],
    ["Add input validation to the signup form…", "Write the missing tests for the parser module…", "Add a --dry-run option to the CLI…"],
    ["Review my recent changes for risky code…", "Check this module for unhandled errors…", "Review the settings code for security issues…"],
]
RECIPES = [  # (mode index, icon, title, description, goal)
    (0, "search", "Map this codebase", "Structure, entry points and key modules, in plain language.", "Map this codebase and explain how it fits together."),
    (0, "alert", "Trace an error", "Follow a failure back to where it starts.", "Trace the cause of the most recent error and explain it."),
    (1, "terminal", "Fix failing tests", "Find the cause, change the code, run the tests again.", "Find why the tests are failing, fix the bug, and run the tests again."),
    (2, "layers", "Add a feature", "Plan it, build it, verify it with tests.", "Plan and implement a new feature, then verify it with tests."),
    (2, "plus", "Write missing tests", "Cover the modules that have no tests yet.", "Write the missing tests for the least-tested modules and run them."),
    (3, "shield", "Review recent changes", "Look for risky code and unhandled errors.", "Review my recent changes for risky code and unhandled errors."),
]
STAGE_INFO = [
    ("Understand", "context", "Phanes reads only what is relevant to your goal. Version-control folders, virtual environments and anything that looks like a secret are never opened.",
     ["Relevant files ranked against your goal", "Secret-looking values masked before anything is used", "You can inspect exactly which paths were considered"]),
    ("Plan", "the work", "Your goal becomes ordered steps with dependencies. Steps that change things are flagged before they run.",
     ["Every step is shown as it is planned", "Consequential steps are marked for approval", "Nothing hides behind a spinner"]),
    ("Execute", "with controlled tools", "Work happens through a fixed set of tools inside your workspace. Anything destructive or external pauses and asks you first.",
     ["Approvals show the exact action and arguments", "The execution engine enforces approval itself, not just this screen", "Cancel at any point"]),
    ("Verify", "what really happened", "Finished is not the same as correct. Every result is labelled verified, unverified or unknown, and says what was actually checked.",
     ["Verified success only when a check passed", "Unverified work is never shown as success", "Unknown outcomes stay unknown"]),
]


class Headline(QWidget):
    """Mixed-typeface headline: sans and serif-italic words, a flowing gradient, word-by-word reveal."""

    def __init__(self, lines, px: int = 56, parent: QWidget | None = None):
        super().__init__(parent)
        self._lines, self._px = lines, px
        self._words: list = []
        self._line_h = int(px * 1.22)
        self._reveal = Tween(self, 0.0)
        self._reveal.changed.connect(lambda _v: self.update())
        self._flow = Loop(self, 10000)
        self._flow.changed.connect(lambda _v: self.update())
        self._flow.start()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._rebuild(900)
        QTimer.singleShot(250, lambda: self._reveal.to(1.0, 1600))

    def _measure(self, px: int):
        words, widest = [], 0
        for line_no, tokens in enumerate(self._lines):
            x = 0
            for text, style in tokens:
                serif = style.startswith("serif")
                font = make_font(px, QFont.Weight.Normal, serif=True, italic=True) if serif else make_font(px, QFont.Weight.DemiBold)
                metrics = QFontMetrics(font)
                for part in re.findall(r"\S+|\s+", text):
                    if not part.isspace():
                        words.append((x, line_no, part, font, style.endswith("_grad"), metrics.ascent()))
                    x += metrics.horizontalAdvance(part)
            widest = max(widest, x)
        return words, widest

    def _rebuild(self, width: int) -> None:
        words, widest = self._measure(self._px)
        scale = min(1.0, max(width - 8, 120) / max(widest, 1))
        px = max(int(self._px * scale), 20)
        if px != self._px:
            words, widest = self._measure(px)
        self._words = words
        self._line_h = int(px * 1.22)
        self.setFixedHeight(self._line_h * len(self._lines) + 12)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rebuild(self.width())

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self._words)
        t = self._reveal.value * (n + 3)
        span = max(self.width(), 420)
        offset = self._flow.value * span * 2  # reflect period is 2*span, so the loop is seamless
        gradient = opal_gradient(offset, 0, offset + span, 0)
        gradient.setSpread(QGradient.Spread.ReflectSpread)
        for i, (x, line_no, text, font, is_grad, ascent) in enumerate(self._words):
            a = max(0.0, min(1.0, t - i))
            if a <= 0:
                continue
            path = QPainterPath()
            path.addText(x, line_no * self._line_h + ascent + 6 + (1 - a) * 18, font, text)
            p.setOpacity(a)
            p.fillPath(path, QBrush(gradient) if is_grad else QColor(TEXT))


class ModeBar(QWidget):
    """'I want to ...' segmented control with a sliding highlight."""

    mode_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setMinimumWidth(4 * 104)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._index, self._hover = -1, -1
        self._font = make_font(13, QFont.Weight.DemiBold)
        self._pos = Tween(self, 0.0, QEasingCurve.Type.OutBack)
        self._on = Tween(self, 0.0)
        for tween in (self._pos, self._on):
            tween.changed.connect(lambda _v: self.update())

    def sizeHint(self) -> QSize:
        return QSize(4 * 112, 44)

    def index(self) -> int:
        return self._index

    def select(self, index: int, emit: bool = True) -> None:
        if index == self._index:
            return
        first = self._index < 0
        self._index = index
        if first:
            self._pos.jump(float(index))
        else:
            self._pos.to(float(index), 520)
        self._on.to(1.0, 300)
        if emit:
            self.mode_selected.emit(index)

    def _at(self, x: float) -> int:
        return max(0, min(len(MODES) - 1, int(x / (self.width() / len(MODES)))))

    def mouseMoveEvent(self, event) -> None:
        i = self._at(event.position().x())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.select(self._at(event.position().x()))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(qc("#FFFFFF", 0.04))
        p.setPen(QPen(qc("#FFFFFF", 0.09), 1))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        seg = r.width() / len(MODES)
        trip = MODES[self._index][1] if self._index >= 0 else MODES[0][1]
        if self._on.value > 0.01:
            pill = QRectF(r.left() + 3 + self._pos.value * seg, r.top() + 3, seg - 6, r.height() - 6)
            p.setBrush(qc(trip[0], 0.16 * self._on.value))
            p.setPen(QPen(qc(trip[0], 0.55 * self._on.value), 1))
            p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        p.setFont(self._font)
        for i, (name, _trip, _prefix) in enumerate(MODES):
            p.setPen(qc(TEXT) if (i == self._index or i == self._hover) else qc(MUTED))
            p.drawText(QRectF(r.left() + i * seg, r.top(), seg, r.height()), Qt.AlignmentFlag.AlignCenter, name)


class LoopTrack(QWidget):
    """Interactive pipeline: click a stage; a light pulse travels the track continuously."""

    selected = Signal(int)
    LABELS = [("01", "Understand", "context"), ("02", "Plan", "the work"), ("03", "Execute", "with tools"), ("04", "Verify", "the result")]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(178)
        self.setMouseTracking(True)
        self._sel = Tween(self, 0.0, QEasingCurve.Type.InOutCubic)
        self._sel.changed.connect(lambda _v: self.update())
        self._index, self._hover = 0, -1
        self._flow = Loop(self, 5200)
        self._flow.changed.connect(lambda _v: self.update())
        self._flow.start()

    def _xs(self) -> list[float]:
        margin, w = 80.0, float(self.width())
        return [margin + i * (w - 2 * margin) / 3 for i in range(4)]

    def _hit(self, pos) -> int:
        for i, x in enumerate(self._xs()):
            if abs(pos.x() - x) < 58 and 8 < pos.y() < 150:
                return i
        return -1

    def select(self, index: int, emit: bool = True) -> None:
        self._index = index
        self._sel.to(float(index), 650)
        if emit:
            self.selected.emit(index)

    def mouseMoveEvent(self, event) -> None:
        i = self._hit(event.position())
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        i = self._hit(event.position())
        if i >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self.select(i)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        xs, y, sel = self._xs(), 52.0, max(0.0, min(3.0, self._sel.value))
        p.setPen(QPen(qc("#FFFFFF", 0.10), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(xs[0], y), QPointF(xs[3], y))
        lo = min(2, int(math.floor(sel)))
        x_sel = xs[lo] + (xs[lo + 1] - xs[lo]) * (sel - lo)
        p.setPen(QPen(QBrush(opal_gradient(xs[0], 0, xs[3], 0)), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(xs[0], y), QPointF(x_sel, y))
        pos = self._flow.value * 3.0
        i = min(2, int(pos))
        px = xs[i] + (xs[i + 1] - xs[i]) * (pos - i)
        p.setPen(Qt.PenStyle.NoPen)
        for radius, alpha in ((16, 0.10), (10, 0.16), (5, 0.55)):
            p.setBrush(qc(MINT, alpha))
            p.drawEllipse(QPointF(px, y), radius, radius)
        for i, (num, name, sub_text) in enumerate(self.LABELS):
            near = max(0.0, 1.0 - abs(i - sel))
            r = 15 + 5 * near + (2 if i == self._hover else 0)
            c = QPointF(xs[i], y)
            p.setBrush(QColor("#0E1320"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(c, r, r)
            if near > 0.01:
                p.setBrush(qc(MINT, 0.10 * near))
                p.drawEllipse(c, r, r)
            ring = QPen(QBrush(opal_gradient(c.x() - r, 0, c.x() + r, 0)), 2.4) if near > 0.35 else QPen(qc("#FFFFFF", 0.26), 1.6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(ring)
            p.drawEllipse(c, r, r)
            p.setFont(make_font(12, QFont.Weight.Bold, mono=True))
            p.setPen(qc(MINT if near > 0.35 else MUTED))
            p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, num)
            p.setFont(make_font(15, QFont.Weight.DemiBold))
            p.setPen(qc(TEXT if near > 0.35 else MUTED))
            p.drawText(QRectF(c.x() - 90, y + 34, 180, 24), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, name)
            p.setFont(make_font(14, QFont.Weight.Normal, serif=True, italic=True))
            p.setPen(qc(MUTED if near > 0.35 else FAINT))
            p.drawText(QRectF(c.x() - 90, y + 58, 180, 22), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, sub_text)


class StageDetail(Glass):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, radius=20, tint=0.045)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(8)
        self.kicker = tag("", MINT)
        self.title = label("", 26, TEXT, "600")
        self.body = label("", 15, MUTED, wrap=True)
        v.addWidget(self.kicker)
        v.addWidget(self.title)
        v.addWidget(self.body)
        v.addSpacing(4)
        self.bullets: list[QLabel] = []
        for _ in range(3):
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(icon_label("check", MINT, 16), 0, Qt.AlignmentFlag.AlignTop)
            text = label("", 14, TEXT, wrap=True)
            row.addWidget(text, 1)
            self.bullets.append(text)
            v.addLayout(row)

    def show_stage(self, index: int, animate: bool = True) -> None:
        sans_part, serif_part, body, bullets = STAGE_INFO[index]
        self.kicker.setText(f"STAGE {index + 1:02d} / 04")
        self.title.setText(f"{sans_part} {em(serif_part, MINT)}")
        self.body.setText(body)
        for widget, text in zip(self.bullets, bullets):
            widget.setText(text)
        if animate:
            fade_in(self, 0, 380)


class FilterChip(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QPushButton{{background:{css('#FFFFFF', 0.04)};color:{MUTED};border:1px solid {css('#FFFFFF', 0.10)};"
            "border-radius:16px;padding:7px 18px;font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{color:{TEXT};}}"
            f"QPushButton:checked{{background:{css(MINT, 0.14)};color:{MINT};border-color:{css(MINT, 0.45)};}}"
        )


class RecipeCard(Glass):
    clicked = Signal()

    def __init__(self, mode: int, icon_name: str, title: str, desc: str, parent: QWidget | None = None):
        super().__init__(parent, radius=18, hoverable=True)
        self.mode = mode
        trip = MODES[mode][1]
        self.accent = trip[0]
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(156)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(tag(MODES[mode][0], trip[0]))
        top.addStretch(1)
        top.addWidget(icon_label(icon_name, trip[0], 18))
        v.addLayout(top)
        v.addSpacing(6)
        parts = (label(title, 17, TEXT, "600"), label(desc, 13, MUTED, wrap=True))
        for widget in parts:
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            v.addWidget(widget)
        v.addStretch(1)
        cta = label(em("Use as a brief →", trip[0]), 13, MUTED)
        cta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        v.addWidget(cta)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def section_head(num: str, title_html: str) -> QWidget:
    widget = QWidget()
    v = QVBoxLayout(widget)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(8)
    row = QHBoxLayout()
    row.setSpacing(12)
    row.addWidget(tag(num, MINT))
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{css('#FFFFFF', 0.08)};border:none;")
    row.addWidget(line, 1)
    v.addLayout(row)
    v.addWidget(label(title_html, 36, TEXT, "600"))
    return widget


class SmoothScroll(QScrollArea):
    """Eased wheel scrolling plus reveal-on-scroll for registered sections."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea{background:transparent;border:none;} QScrollArea>QWidget>QWidget{background:transparent;}")
        self._tw = Tween(self, 0.0, QEasingCurve.Type.OutCubic)
        self._tw.changed.connect(lambda v: self.verticalScrollBar().setValue(int(v)))
        self._tw.finished.connect(self._done)
        self._animating, self._target = False, 0.0
        self._pending: list[QWidget] = []
        self._ready = False
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._check_reveal())

    def _done(self) -> None:
        self._animating = False

    def _animate_to(self, y: float, ms: int) -> None:
        bar = self.verticalScrollBar()
        self._target = max(0.0, min(float(bar.maximum()), y))
        self._animating = True
        self._tw.value = float(bar.value())
        self._tw.to(self._target, ms)

    def wheelEvent(self, event) -> None:
        base = self._target if self._animating else float(self.verticalScrollBar().value())
        self._animate_to(base - event.angleDelta().y() * 0.95, 360)
        event.accept()

    def scroll_to(self, y: float) -> None:
        self._animate_to(float(y), 700)

    def add_reveal(self, widget: QWidget) -> None:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        self._pending.append(widget)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(80, self._arm)

    def _arm(self) -> None:
        self._ready = True
        self._check_reveal()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._check_reveal()

    def _check_reveal(self) -> None:
        if not self._ready or not self._pending:
            return
        limit = self.viewport().height() - 50
        for widget in list(self._pending):
            if widget.mapTo(self.viewport(), QPoint(0, 0)).y() < limit:
                self._pending.remove(widget)
                fade_in(widget, 0, 700)


class PaletteEdit(QLineEdit):
    move = Signal(int)
    chosen = Signal()
    escaped = Signal()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Down:
            self.move.emit(1)
        elif key == Qt.Key.Key_Up:
            self.move.emit(-1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.chosen.emit()
        elif key == Qt.Key.Key_Escape:
            self.escaped.emit()
        else:
            super().keyPressEvent(event)


class CommandPalette(QWidget):
    """Ctrl+K overlay: type to filter commands, arrows to move, Enter to run."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.hide()
        self._entries: list = []
        self._shown: list = []
        self._closing = False
        self._fade = Tween(self, 0.0)
        self._fade.changed.connect(self._on_fade)
        self.panel = QFrame(self)
        self.panel.setObjectName("palettePanel")
        self.panel.setStyleSheet(f"QFrame#palettePanel{{background:#0C101A;border:1px solid {css('#FFFFFF', 0.13)};border-radius:18px;}}")
        self._fx = QGraphicsOpacityEffect(self.panel)
        self.panel.setGraphicsEffect(self._fx)
        v = QVBoxLayout(self.panel)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)
        self.edit = PaletteEdit()
        self.edit.setPlaceholderText("Type a command or search…")
        self.edit.setFont(make_font(17))
        self.edit.setStyleSheet(f"QLineEdit{{background:transparent;border:none;color:{TEXT};padding:8px 6px;}}")
        v.addWidget(self.edit)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{css('#FFFFFF', 0.08)};border:none;")
        v.addWidget(sep)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setStyleSheet(
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{border-radius:10px;}"
            f"QListWidget::item:selected{{background:{css(MINT, 0.12)};}}"
            f"QListWidget::item:hover{{background:{css('#FFFFFF', 0.05)};}}"
        )
        v.addWidget(self.list, 1)
        v.addWidget(label("↑ ↓ navigate   ·   Enter run   ·   Esc close", 11, FAINT, face="mono"))
        self.edit.textChanged.connect(self._populate)
        self.edit.move.connect(self._move)
        self.edit.chosen.connect(self._choose)
        self.edit.escaped.connect(self.close_palette)
        self.list.itemClicked.connect(lambda _item: self._choose())

    def open(self, entries: list) -> None:
        self._entries, self._closing = entries, False
        self.edit.clear()
        self._populate("")
        self.setGeometry(self.parentWidget().rect())
        self._place()
        self.show()
        self.raise_()
        self._fade.to(1.0, 260)
        self.edit.setFocus()

    def close_palette(self) -> None:
        if self.isVisible() and not self._closing:
            self._closing = True
            self._fade.to(0.0, 180)

    def is_open(self) -> bool:
        return self.isVisible() and not self._closing

    def _on_fade(self, value: float) -> None:
        self._fx.setOpacity(value)
        self.update()
        if self._closing and value <= 0.01:
            self.hide()

    def _place(self) -> None:
        w, h = min(640, self.width() - 40), min(450, self.height() - 120)
        self.panel.setGeometry((self.width() - w) // 2, max(60, int(self.height() * 0.15)), w, h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def _populate(self, query: str) -> None:
        tokens = query.lower().split()
        self.list.clear()
        self._shown = []
        for title, hint, callback in self._entries:
            if all(t in f"{title} {hint}".lower() for t in tokens):
                self._shown.append((title, hint, callback))
                item = QListWidgetItem()
                item.setSizeHint(QSize(0, 42))
                self.list.addItem(item)
                row = QWidget()
                layout = QHBoxLayout(row)
                layout.setContentsMargins(12, 0, 12, 0)
                layout.addWidget(label(title, 14, TEXT, "500"))
                layout.addStretch(1)
                layout.addWidget(label(hint, 11, MUTED, face="mono"))
                row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self.list.setItemWidget(item, row)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _move(self, delta: int) -> None:
        n = self.list.count()
        if n:
            self.list.setCurrentRow(max(0, min(n - 1, self.list.currentRow() + delta)))

    def _choose(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._shown):
            callback = self._shown[row][2]
            self.close_palette()
            QTimer.singleShot(220, callback)

    def mousePressEvent(self, event) -> None:
        if not self.panel.geometry().contains(event.position().toPoint()):
            self.close_palette()

    def paintEvent(self, _event) -> None:
        QPainter(self).fillRect(self.rect(), QColor(2, 4, 8, int(165 * self._fade.value)))


# =============================================================================
# 5. COMPOSER (animated border) and NAV
# =============================================================================
class GoalEdit(QPlainTextEdit):
    submit_shortcut = Signal()
    focus_changed = Signal(bool)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.submit_shortcut.emit()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(False)


class Composer(QFrame):
    """The brief. A rotating border in the current mode's colours fades in while focused."""

    submitted = Signal(str)
    MAX_CHARS = 4000

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.ready, self._reason, self.ignore_text = False, "", ""
        self._trip = MODES[0][1]
        self._suggestions = MODE_SUGGESTIONS[0]
        self._focus = Tween(self, 0.0, QEasingCurve.Type.InOutCubic)
        self._focus.changed.connect(lambda _v: self.update())
        self._spin = Loop(self, 7000)
        self._spin.changed.connect(lambda _v: self.update())

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 18, 14)
        root.setSpacing(8)
        header = QHBoxLayout()
        header.addWidget(tag("The brief", MINT))
        header.addStretch(1)
        header.addWidget(label(em("say it the way you'd tell a colleague"), 13, FAINT))
        root.addLayout(header)

        self.error = label("", 12, CORAL, "600", wrap=True)
        self.error.hide()
        root.addWidget(self.error)

        self.edit = GoalEdit()
        self.edit.setFont(make_font(17))
        self.edit.setMinimumHeight(120)
        self.edit.setMaximumHeight(240)
        self.edit.setFrameShape(QFrame.Shape.NoFrame)
        self.edit.setTabChangesFocus(True)
        self.edit.setStyleSheet(f"QPlainTextEdit{{background:transparent;color:{TEXT};border:none;selection-background-color:{css(IRIS, 0.35)};}}")
        palette = self.edit.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, qc(MUTED, 0.75))
        self.edit.setPalette(palette)
        self.edit.textChanged.connect(self._on_text)
        self.edit.focus_changed.connect(self._on_focus)
        self.edit.submit_shortcut.connect(self._submit)
        root.addWidget(self.edit, 1)

        footer = QHBoxLayout()
        self.hint = label("Ctrl + Enter to run", 12, FAINT, face="mono")
        footer.addWidget(self.hint)
        footer.addStretch(1)
        self.counter = label("", 12, MUTED, face="mono")
        footer.addWidget(self.counter)
        self.run_button = GradientButton("Run task")
        self.run_button.clicked.connect(self._submit)
        footer.addWidget(self.run_button)
        root.addLayout(footer)

        self._ph_index, self._ph_pos, self._ph_phase, self._ph_hold = 0, 0, "type", 0
        self._ph_timer = QTimer(self)
        self._ph_timer.setInterval(34)
        self._ph_timer.timeout.connect(self._type_step)
        self._ph_timer.start()
        self._refresh()

    # -- public ---------------------------------------------------------------
    def goal(self) -> str:
        return self.edit.toPlainText().strip()

    def has_goal(self) -> bool:
        return self.goal() not in ("", self.ignore_text.strip())

    def set_text(self, text: str) -> None:
        self.edit.setPlainText(text)
        self.edit.moveCursor(QTextCursor.MoveOperation.End)
        self.edit.setFocus()

    def clear(self) -> None:
        self.ignore_text = ""
        self.edit.clear()
        self.show_error("")

    def set_ready(self, ready: bool, reason: str = "") -> None:
        self.ready, self._reason = ready, reason
        self._refresh()

    def set_trip(self, trip) -> None:
        self._trip = tuple(trip)
        self.update()

    def set_suggestions(self, suggestions: list[str]) -> None:
        self._suggestions = suggestions
        self._ph_index, self._ph_pos, self._ph_phase = 0, 0, "type"

    def show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))
        if message:
            fade_in(self.error, 0, 350)

    # -- internals --------------------------------------------------------------
    def _refresh(self) -> None:
        text_ok = self.has_goal() and len(self.edit.toPlainText()) <= self.MAX_CHARS
        self.run_button.setEnabled(self.ready and text_ok)
        self.hint.setText(self._reason if (self._reason and not self.ready) else "Ctrl + Enter to run")
        n = len(self.edit.toPlainText())
        self.counter.setText(f"{n}/{self.MAX_CHARS}" if n > self.MAX_CHARS * 0.8 else "")

    def _on_text(self) -> None:
        if not self.error.isHidden():
            self.show_error("")
        self._refresh()

    def _on_focus(self, focused: bool) -> None:
        self._focus.to(1.0 if focused else 0.0, 450)
        if focused:
            self._spin.start()
        else:
            QTimer.singleShot(500, lambda: None if self.edit.hasFocus() else self._spin.stop())

    def _submit(self) -> None:
        if self.run_button.isEnabled():
            self.submitted.emit(self.goal())

    def _type_step(self) -> None:
        if self.edit.toPlainText():
            return
        text = self._suggestions[self._ph_index % len(self._suggestions)]
        if self._ph_phase == "type":
            self._ph_pos += 1
            if self._ph_pos >= len(text):
                self._ph_phase, self._ph_hold = "hold", 60
        elif self._ph_phase == "hold":
            self._ph_hold -= 1
            if self._ph_hold <= 0:
                self._ph_phase = "erase"
        else:
            self._ph_pos -= 2
            if self._ph_pos <= 0:
                self._ph_pos, self._ph_phase = 0, "type"
                self._ph_index = (self._ph_index + 1) % len(self._suggestions)
        self.edit.setPlaceholderText(text[: max(self._ph_pos, 0)] or " ")

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        p.setBrush(qc("#FFFFFF", 0.045))
        p.setPen(QPen(qc("#FFFFFF", 0.11), 1))
        p.drawRoundedRect(rect, 24, 24)
        f = self._focus.value
        if f > 0.01:
            ring = QConicalGradient(rect.center(), -self._spin.value * 360)
            ring.setColorAt(0.0, qc(self._trip[0]))
            ring.setColorAt(0.33, qc(self._trip[1]))
            ring.setColorAt(0.66, qc(self._trip[2]))
            ring.setColorAt(1.0, qc(self._trip[0]))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setOpacity(0.16 * f)
            p.setPen(QPen(QBrush(ring), 7))
            p.drawRoundedRect(rect, 24, 24)
            p.setOpacity(f)
            p.setPen(QPen(QBrush(ring), 1.6))
            p.drawRoundedRect(rect, 24, 24)


class NavItem(QPushButton):
    def __init__(self, text: str, icon_name: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setFixedHeight(42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(make_font(14, QFont.Weight.Medium))
        self._icons = {
            "on": svg_pixmap(icon_name, MINT, 18),
            "off": svg_pixmap(icon_name, MUTED, 18),
            "dim": svg_pixmap(icon_name, FAINT, 18),
        }
        self.hover = Tween(self)
        self.hover.changed.connect(lambda _v: self.update())
        self.slide = Tween(self, 0.0, QEasingCurve.Type.OutBack)
        self.slide.changed.connect(lambda _v: self.update())
        self.toggled.connect(lambda on: self.slide.to(1.0 if on else 0.0, 380))

    def enterEvent(self, event) -> None:
        self.hover.to(1.0, 160)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.hover.to(0.0, 240)
        super().leaveEvent(event)

    def set_active(self, active: bool) -> None:
        self.setChecked(active)
        self.slide.jump(1.0 if active else 0.0)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        active, enabled, h = self.slide.value, self.isEnabled(), self.hover.value
        if active > 0.01:
            wash = QLinearGradient(rect.left(), 0, rect.right(), 0)
            wash.setColorAt(0.0, qc(MINT, 0.15 * active))
            wash.setColorAt(1.0, qc(IRIS, 0.05 * active))
            p.setBrush(QBrush(wash))
            p.setPen(QPen(qc(MINT, 0.22 * active), 1))
            p.drawRoundedRect(rect, 12, 12)
            bar = QRectF(rect.left() + 1, rect.center().y() - 9 * active, 3, 18 * active)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(opal_gradient(0, bar.top(), 0, bar.bottom())))
            p.drawRoundedRect(bar, 1.5, 1.5)
        elif enabled and h > 0.01:
            p.setBrush(qc("#FFFFFF", 0.05 * h))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, 12, 12)
        key = "on" if (self.isChecked() and enabled) else ("off" if enabled else "dim")
        p.drawPixmap(16, int(rect.center().y() - 9 + 1), self._icons[key])
        p.setFont(self.font())
        p.setPen(qc(TEXT) if (self.isChecked() and enabled) else qc(MUTED if enabled else FAINT))
        p.drawText(QRectF(44, 0, self.width() - 52, self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


# =============================================================================
# 6. APPROVAL DIALOG
# =============================================================================
class ApprovalDialog(QDialog):
    """Shows the actual proposed action. Closing it is NOT a decision."""

    decided = Signal(bool)

    def __init__(self, ev: ApprovalEv, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Approval required")
        self.setModal(True)
        self.setMinimumWidth(540)
        self.setStyleSheet(f"QDialog{{background:#0B0F18;}} QLabel{{background:transparent;}}")
        self._decided = False
        self._opacity = Tween(self, 0.0)
        self._opacity.changed.connect(self.setWindowOpacity)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(icon_label("shield", AMBER, 26))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("Approval required", 20, TEXT, "600"))
        titles.addWidget(label("Review exactly what will happen before it does.", 13, MUTED))
        head.addLayout(titles, 1)
        root.addLayout(head)

        card = Glass(radius=14, tint=0.05)
        grid = QVBoxLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setSpacing(6)
        grid.addWidget(label("PROPOSED ACTION", 11, AMBER, "700"))
        grid.addWidget(label(ev.summary, 14, TEXT, "500", wrap=True))
        grid.addWidget(label(f"Tool  ·  {ev.tool}        Step  ·  {ev.step_id}", 12, MUTED))
        root.addWidget(card)

        root.addWidget(label("ARGUMENTS", 11, MUTED, "700"))
        code = QPlainTextEdit()
        code.setReadOnly(True)
        code.setFont(make_font(12, mono=True))
        code.setMaximumHeight(130)
        code.setStyleSheet(f"QPlainTextEdit{{background:{css('#000000', 0.35)};color:{TEXT};border:1px solid {css('#FFFFFF', 0.09)};border-radius:10px;padding:8px;}}")
        lines, hidden = [], False
        for key, value in ev.arguments.items():
            secret = any(w in key.lower() for w in ("token", "secret", "password", "key", "credential"))
            hidden = hidden or secret
            lines.append(f"{key}: {'[hidden]' if secret else value}")
        code.setPlainText("\n".join(lines) or "(no arguments)")
        root.addWidget(code)
        if hidden:
            root.addWidget(label("Values of secret-looking keys are hidden in this view only.", 11, FAINT))
        root.addWidget(label("Closing this window neither approves nor rejects. The execution engine enforces approval independently of this screen.", 12, MUTED, wrap=True))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reject = GhostButton("Reject", "danger", "x")
        reject.setAutoDefault(False)
        reject.clicked.connect(lambda: self._decide(False))
        approve = GradientButton("Approve")
        approve.setAutoDefault(False)
        approve.clicked.connect(lambda: self._decide(True))
        buttons.addWidget(reject)
        buttons.addWidget(approve)
        root.addLayout(buttons)
        reject.setFocus()  # the safe choice is the default focus

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setWindowOpacity(0.0)
        self._opacity.to(1.0, 260)

    def _decide(self, approved: bool) -> None:
        if self._decided:
            return
        self._decided = True
        self.decided.emit(approved)
        self.accept()


# =============================================================================
# 7. PAGES
# =============================================================================
@dataclass
class TaskState:
    goal: str = ""
    task_id: str | None = None
    status: str | None = None
    message: str | None = None
    steps: list[StepVM] = field(default_factory=list)
    approval: ApprovalEv | None = None
    cancelling: bool = False
    started: float = 0.0

    def step(self, step_id: str) -> StepVM | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    @property
    def active(self) -> bool:
        return self.task_id is not None and self.status not in TERMINAL

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.steps:
            if s.result is not None:
                key = classify(s.result)[0]
                out[key] = out.get(key, 0) + 1
        return out


class StepRow(QWidget):
    def __init__(self, step: StepVM, last: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.step_id = step.step_id
        self._last = last
        self._filled = Tween(self)
        self._filled.changed.connect(lambda _v: self.update())
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 6, 0, 10)
        row.setSpacing(14)
        left = QVBoxLayout()
        self.node = StepNode(30)
        left.addWidget(self.node)
        left.addStretch(1)
        row.addLayout(left)
        body = QVBoxLayout()
        body.setSpacing(3)
        title_row = QHBoxLayout()
        self.title = label(step.title, 15, TEXT, "600")
        title_row.addWidget(self.title)
        title_row.addStretch(1)
        self.status_pill = Pill("Pending", "muted")
        self.outcome_pill = Pill("", "muted")
        self.outcome_pill.hide()
        title_row.addWidget(self.status_pill)
        title_row.addWidget(self.outcome_pill)
        body.addLayout(title_row)
        meta = f"{step.tool}" + ("  ·  flagged for approval" if step.requires_approval else "")
        body.addWidget(label(meta, 12, FAINT))
        self.message = label("", 12, MUTED, wrap=True)
        self.message.hide()
        body.addWidget(self.message)
        self.detail = label("", 12, MUTED, wrap=True)
        self.detail.hide()
        body.addWidget(self.detail)
        row.addLayout(body, 1)
        self.update_from(step)

    def update_from(self, step: StepVM) -> None:
        self.node.set_state(node_state_for(step))
        text, tone = STATUS_LABEL.get(step.status, (step.status, "muted"))
        self.status_pill.set(text, tone)
        self.message.setText(step.message or "")
        self.message.setVisible(bool(step.message))
        if step.result is not None:
            outcome, otone = classify(step.result)
            self.outcome_pill.set(outcome, otone)
            self.outcome_pill.show()
            verdict = "Verified. " if step.result.verified else "Not verified. "
            self.detail.setText(verdict + (step.result.details or "The backend did not report what was checked."))
            self.detail.show()
        elif step.status == "completed":
            self.outcome_pill.set("No result reported", "muted")
            self.outcome_pill.show()
        self._filled.to(1.0 if step.status == "completed" else 0.0, 500)

    def paintEvent(self, _event) -> None:
        if self._last:
            return
        p = QPainter(self)
        x, top = 15, 40
        p.setPen(QPen(qc("#FFFFFF", 0.09), 1.5))
        p.drawLine(x, top, x, self.height())
        if self._filled.value > 0.01:
            p.setPen(QPen(qc(MINT, 0.55), 1.5))
            p.drawLine(x, top, x, int(top + (self.height() - top) * self._filled.value))


class HomePage(QWidget):
    """A scrolling story: the brief first, then how it works, starting points, guardrails."""

    goal_submitted = Signal(str)
    pick_workspace = Signal()
    mode_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._prefix = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = SmoothScroll()
        outer.addWidget(self.scroll)
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(8, 0, 8, 0)
        row.addStretch(1)
        column = QWidget()
        column.setMaximumWidth(1040)
        column.setMinimumWidth(520)
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 12, 0, 80)
        col.setSpacing(0)
        row.addWidget(column, 100)
        row.addStretch(1)
        self.scroll.setWidget(page)

        # ---- 01: hero + brief ----------------------------------------------------
        hero = QWidget()
        h = QVBoxLayout(hero)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        eyebrow = QHBoxLayout()
        eyebrow.setSpacing(8)
        eyebrow.addWidget(PulseDot(MINT))
        eyebrow.addWidget(tag("Phanes AI-OS · developer workstation", MINT))
        eyebrow.addStretch(1)
        h.addLayout(eyebrow)
        h.addSpacing(12)
        self.headline = Headline(
            [
                [("Give it a ", "sans"), ("goal", "serif_grad"), (".", "sans")],
                [("Keep the ", "sans"), ("judgment", "serif_grad"), (".", "sans")],
            ],
            58,
        )
        h.addWidget(self.headline)
        h.addSpacing(14)
        self.sub = label(
            f"Phanes {em('plans', MINT)} the work, does it with controlled tools, and tells you what was {em('verified', MINT)}, "
            f"not just what finished. It {em('asks first', MINT)} before anything consequential.",
            17,
            MUTED,
            wrap=True,
        )
        self.sub.setMaximumWidth(740)
        h.addWidget(self.sub)
        h.addSpacing(30)

        self.workspace_card = Glass(radius=16, hoverable=True)
        wrow = QHBoxLayout(self.workspace_card)
        wrow.setContentsMargins(18, 12, 12, 12)
        wrow.setSpacing(14)
        wrow.addWidget(icon_label("folder", MINT, 20))
        wcol = QVBoxLayout()
        wcol.setSpacing(0)
        self.ws_name = label("No workspace selected", 15, TEXT, "600")
        self.ws_path = Elided("Choose a project folder to give Phanes its context.", 12, MUTED)
        wcol.addWidget(self.ws_name)
        wcol.addWidget(self.ws_path)
        wrow.addLayout(wcol, 1)
        self.ws_button = GhostButton("Select workspace", "info", "folder")
        self.ws_button.clicked.connect(self.pick_workspace)
        wrow.addWidget(self.ws_button)
        h.addWidget(self.workspace_card)
        h.addSpacing(22)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(16)
        mode_row.addWidget(label(em("I want to", MUTED), 22, MUTED))
        self.modebar = ModeBar()
        self.modebar.mode_selected.connect(self._on_mode)
        mode_row.addWidget(self.modebar)
        mode_row.addStretch(1)
        h.addLayout(mode_row)
        h.addSpacing(14)

        self.composer = Composer()
        self.composer.submitted.connect(self.goal_submitted)
        h.addWidget(self.composer)
        h.addSpacing(12)
        h.addWidget(label("Scroll for how it works  ·  Ctrl K opens commands", 12, FAINT, face="mono"))
        col.addWidget(hero)
        col.addSpacing(96)

        # ---- 02: the loop ----------------------------------------------------------
        self.sec_loop = QWidget()
        s2 = QVBoxLayout(self.sec_loop)
        s2.setContentsMargins(0, 0, 0, 0)
        s2.setSpacing(18)
        s2.addWidget(section_head("02", f"How a brief {em('runs', MINT)}"))
        self.track = LoopTrack()
        self.detail = StageDetail()
        self.track.selected.connect(self.detail.show_stage)
        self.detail.show_stage(0, animate=False)
        s2.addWidget(self.track)
        s2.addWidget(self.detail)
        col.addWidget(self.sec_loop)
        col.addSpacing(96)

        # ---- 03: starting points ---------------------------------------------------
        self.sec_recipes = QWidget()
        s3 = QVBoxLayout(self.sec_recipes)
        s3.setContentsMargins(0, 0, 0, 0)
        s3.setSpacing(18)
        s3.addWidget(section_head("03", f"Starting {em('points', MINT)}"))
        chips = QHBoxLayout()
        chips.setSpacing(8)
        group = QButtonGroup(self)
        for i, name in enumerate(["All"] + [m[0] for m in MODES]):
            chip = FilterChip(name)
            chip.setChecked(i == 0)
            group.addButton(chip, i)
            chip.clicked.connect(lambda _c=False, k=i: self._filter(k - 1))
            chips.addWidget(chip)
        chips.addStretch(1)
        s3.addLayout(chips)
        self.grid = QGridLayout()
        self.grid.setSpacing(14)
        for c in range(3):
            self.grid.setColumnStretch(c, 1)
        self.cards: list[RecipeCard] = []
        for mode, icon_name, title, desc, goal in RECIPES:
            card = RecipeCard(mode, icon_name, title, desc)
            card.clicked.connect(lambda m=mode, g=goal: self.use_recipe(m, g))
            self.cards.append(card)
        s3.addLayout(self.grid)
        self._filter(-1, animate=False)
        col.addWidget(self.sec_recipes)
        col.addSpacing(96)

        # ---- 04: guardrails -----------------------------------------------------------
        self.sec_trust = QWidget()
        s4 = QVBoxLayout(self.sec_trust)
        s4.setContentsMargins(0, 0, 0, 0)
        s4.setSpacing(18)
        s4.addWidget(section_head("04", f"Built to be {em('trusted', MINT)}"))
        trio = QHBoxLayout()
        trio.setSpacing(14)
        for icon_name, title, text in (
            ("shield", f"Asks {em('first', MINT)}", "Destructive or external actions pause for you, showing the exact action and arguments."),
            ("folder", f"Stays {em('in scope', MINT)}", "Work is limited to the workspace you chose. Secrets and version-control folders are never opened."),
            ("check", f"Verified, {em('not assumed', MINT)}", "Finished and correct are different. Results say what was checked, or that nothing was."),
        ):
            card = Glass(radius=18, hoverable=True)
            v = QVBoxLayout(card)
            v.setContentsMargins(20, 18, 20, 18)
            v.setSpacing(8)
            v.addWidget(icon_label(icon_name, MINT, 22))
            v.addSpacing(4)
            title_label = label(title, 20, TEXT, "600")
            body = label(text, 14, MUTED, wrap=True)
            for w in (title_label, body):
                w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                v.addWidget(w)
            v.addStretch(1)
            trio.addWidget(card, 1)
        s4.addLayout(trio)
        col.addWidget(self.sec_trust)
        col.addSpacing(96)

        # ---- 05: footer ----------------------------------------------------------------
        self.sec_foot = QWidget()
        s5 = QVBoxLayout(self.sec_foot)
        s5.setContentsMargins(0, 0, 0, 0)
        s5.setSpacing(16)
        s5.addWidget(label(f"Finished is not the same as {em('verified', MINT)}.", 30, TEXT, "500", wrap=True))
        keys = QHBoxLayout()
        keys.setSpacing(8)
        for text in ("Ctrl K · commands", "Ctrl Enter · run", "Ctrl Shift A · activity", "Ctrl N · new task"):
            keys.addWidget(Pill(text, "muted"))
        keys.addStretch(1)
        s5.addLayout(keys)
        col.addWidget(self.sec_foot)
        col.addStretch(1)

        for section in (self.sec_loop, self.sec_recipes, self.sec_trust, self.sec_foot):
            self.scroll.add_reveal(section)
        self.set_workspace(None)

    # -- workspace -------------------------------------------------------------------
    def set_workspace(self, path: Path | None) -> None:
        if path is None:
            self.ws_name.setText("No workspace selected")
            self.ws_path.set_full("Choose a project folder to give Phanes its context.")
            self.composer.set_ready(False, "Select a workspace to begin.")
        else:
            self.ws_name.setText(path.name or str(path))
            self.ws_path.set_full(str(path))
            self.composer.set_ready(True)
        self.ws_button.setText("Change workspace" if path else "Select workspace")

    # -- intent modes ------------------------------------------------------------------
    def _mode_visuals(self, index: int) -> None:
        trip = MODES[index][1]
        self.composer.set_trip(trip)
        self.composer.set_suggestions(MODE_SUGGESTIONS[index])
        self.mode_changed.emit(trip)

    def _on_mode(self, index: int) -> None:
        self._mode_visuals(index)
        new_prefix, old = MODES[index][2], self._prefix
        text = self.composer.edit.toPlainText()
        self._prefix = new_prefix
        self.composer.ignore_text = new_prefix  # set first: a bare prefix must never count as a goal
        if not text.strip() or text.strip() == old.strip():
            self.composer.set_text(new_prefix)
        elif old and text.startswith(old):
            self.composer.set_text(new_prefix + text[len(old):])
        else:
            self.composer.edit.setFocus()
            self.composer.set_ready(self.composer.ready, "")  # re-evaluate Run with the new ignore_text

    def use_recipe(self, mode: int, goal: str) -> None:
        self.modebar.select(mode, emit=False)
        self._mode_visuals(mode)
        self._prefix = MODES[mode][2]
        self.composer.ignore_text = self._prefix
        self.composer.set_text(self._prefix + goal)
        self.scroll.scroll_to(0)

    def _filter(self, mode: int, animate: bool = True) -> None:
        for card in self.cards:
            self.grid.removeWidget(card)
            card.hide()
        shown = [c for c in self.cards if mode < 0 or c.mode == mode]
        for i, card in enumerate(shown):
            self.grid.addWidget(card, i // 3, i % 3)
            card.show()
            if animate:
                fade_in(card, 60 * i, 450)

    # -- scrolling -----------------------------------------------------------------------
    def scroll_top(self) -> None:
        self.scroll.scroll_to(0)

    def scroll_to_recipes(self) -> None:
        y = self.sec_recipes.mapTo(self.scroll.widget(), QPoint(0, 0)).y()
        self.scroll.scroll_to(y - 16)

    def entrance(self) -> list[QPropertyAnimation]:
        targets = [self.sub, self.workspace_card, self.modebar, self.composer]
        return [fade_in(w, 500 + 130 * i, 700) for i, w in enumerate(targets)]


class RunPage(QWidget):
    back_requested = Signal()
    cancel_requested = Signal()
    review_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: dict[str, StepRow] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 10, 8, 0)
        outer.setSpacing(10)

        top = QHBoxLayout()
        self.back = GhostButton("New task", "neutral", "arrow-left")
        self.back.clicked.connect(self.back_requested)
        top.addWidget(self.back)
        top.addStretch(1)
        self.status = Pill("Submitting", "info", dot=True)
        top.addWidget(self.status)
        self.cancel = GhostButton("Cancel task", "danger", "stop")
        self.cancel.clicked.connect(self.cancel_requested)
        top.addWidget(self.cancel)
        outer.addLayout(top)

        self.scroll = SmoothScroll()
        outer.addWidget(self.scroll, 1)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 8, 0, 48)
        root.setSpacing(16)
        root.addWidget(tag("The brief", MINT))
        self.goal = label("", 28, TEXT, "600", wrap=True)
        root.addWidget(self.goal)
        self.summary = label("", 13, MUTED, face="mono")
        root.addWidget(self.summary)
        self.bar = GlowBar()
        root.addWidget(self.bar)

        self.banner = Glass(radius=14, tint=0.05)
        brow = QHBoxLayout(self.banner)
        brow.setContentsMargins(16, 12, 12, 12)
        bcol = QVBoxLayout()
        bcol.setSpacing(2)
        self.banner_title = label("", 14, TEXT, "600")
        self.banner_text = label("", 13, MUTED, wrap=True)
        bcol.addWidget(self.banner_title)
        bcol.addWidget(self.banner_text)
        brow.addLayout(bcol, 1)
        self.banner_action = GhostButton("Review request", "warning", "shield")
        self.banner_action.clicked.connect(self.review_requested)
        brow.addWidget(self.banner_action)
        self.banner.hide()
        root.addWidget(self.banner)

        plan = Glass(radius=18)
        pl = QVBoxLayout(plan)
        pl.setContentsMargins(22, 18, 22, 14)
        pl.setSpacing(4)
        pl.addWidget(tag("The plan", MUTED))
        self.empty = label(em("Waiting for the plan from the backend…"), 14, MUTED)
        pl.addWidget(self.empty)
        self.timeline = QVBoxLayout()
        self.timeline.setSpacing(0)
        pl.addLayout(self.timeline)
        root.addWidget(plan)

        logcard = Glass(radius=18)
        ll = QVBoxLayout(logcard)
        ll.setContentsMargins(22, 18, 22, 16)
        ll.addWidget(tag("Activity log", MUTED))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setMinimumHeight(260)
        self.log.setFont(make_font(12, mono=True))
        self.log.setStyleSheet(f"QPlainTextEdit{{background:transparent;border:none;color:{MUTED};}}")
        ll.addWidget(self.log)
        root.addWidget(logcard)
        self.scroll.setWidget(content)

    def reset(self, goal: str) -> None:
        self.goal.setText(goal)
        self._clear_rows()
        self.empty.show()
        self.log.clear()
        self.banner.hide()
        self.summary.setText("")
        self.scroll.scroll_to(0)

    def _clear_rows(self) -> None:
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

    def rebuild(self, steps: list[StepVM]) -> None:
        self.empty.hide()
        self._clear_rows()
        for i, step in enumerate(steps):
            row = StepRow(step, last=i == len(steps) - 1)
            self._rows[step.step_id] = row
            self.timeline.addWidget(row)
            fade_in(row, 80 * i, 500)

    def update_step(self, step: StepVM) -> None:
        row = self._rows.get(step.step_id)
        if row:
            row.update_from(step)

    def append_log(self, text: str, level: str = "info") -> None:
        colors = {"info": MUTED, "stdout": TEXT, "stderr": CORAL, "warning": AMBER, "error": CORAL}
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colors.get(level, MUTED)))
        bar = self.log.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(f"{datetime.now().strftime('%H:%M:%S')}   {text}\n", fmt)
        if at_bottom:
            bar.setValue(bar.maximum())

    def show_banner(self, tone: str, title: str, text: str, action: bool = False) -> None:
        color = TONES.get(tone, MUTED)
        self.banner.accent = color
        self.banner.tone_mix = 0.7
        self.banner_title.setStyleSheet(f"color:{color}; font-size:14px; font-weight:600; background:transparent;")
        self.banner_title.setText(title)
        self.banner_text.setText(text)
        self.banner_text.setVisible(bool(text))
        self.banner_action.setVisible(action)
        was_hidden = self.banner.isHidden()
        self.banner.show()
        self.banner.update()
        if was_hidden:
            fade_in(self.banner, 0, 400)

    def hide_banner(self) -> None:
        self.banner.hide()


class ActivityPanel(Glass):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, radius=22)
        self.setFixedWidth(300)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 18)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(label("Activity", 16, TEXT, "600"))
        head.addStretch(1)
        self.dot = PulseDot(FAINT)
        head.addWidget(self.dot)
        self.state_pill = Pill("Idle", "muted")
        head.addWidget(self.state_pill)
        root.addLayout(head)
        root.addSpacing(4)
        root.addWidget(label("OVERVIEW", 11, MUTED, "700"))

        grid = QHBoxLayout()
        grid.setSpacing(8)
        self.m_steps = self._metric("Steps", "0 / 0")
        self.m_time = self._metric("Elapsed", "00:00")
        self.m_ok = self._metric("Verified", "0")
        for m in (self.m_steps, self.m_time, self.m_ok):
            grid.addWidget(m[0])
        root.addLayout(grid)
        root.addSpacing(6)

        root.addWidget(label("WORKFLOW", 11, MUTED, "700"))
        self.stage_nodes: list[StepNode] = []
        for name in STAGES:
            row = QHBoxLayout()
            row.setSpacing(10)
            node = StepNode(22)
            self.stage_nodes.append(node)
            row.addWidget(node)
            row.addWidget(label(name, 13, TEXT, "500"))
            row.addStretch(1)
            root.addLayout(row)

        root.addSpacing(6)
        root.addWidget(label("VERIFICATION", 11, MUTED, "700"))
        self.verif = label("Nothing to verify yet.", 13, MUTED, wrap=True)
        root.addWidget(self.verif)
        root.addStretch(1)

        self.sim_card = Glass(radius=14, tint=0.04)
        self.sim_card.accent = AMBER
        self.sim_card.tone_mix = 0.55
        sl = QVBoxLayout(self.sim_card)
        sl.setContentsMargins(14, 12, 14, 12)
        sl.setSpacing(3)
        sl.addWidget(label("Simulated backend", 13, AMBER, "600"))
        sl.addWidget(label("The planner and execution engine are not connected. Everything shown for tasks is scripted; nothing touches your files.", 12, MUTED, wrap=True))
        root.addWidget(self.sim_card)

    def _metric(self, name: str, value: str) -> tuple[Glass, QLabel]:
        card = Glass(radius=12, tint=0.035)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(0)
        val = label(value, 15, TEXT, "600")
        layout.addWidget(val)
        layout.addWidget(label(name, 11, MUTED))
        return card, val

    def refresh(self, st: TaskState | None) -> None:
        if st is None or st.task_id is None and st.status is None:
            self.state_pill.set("Idle", "muted")
            self.dot.set_color(FAINT)
            self.m_steps[1].setText("0 / 0")
            self.m_ok[1].setText("0")
            for node in self.stage_nodes:
                node.set_state("pending")
            self.verif.setText("Nothing to verify yet.")
            return
        label_text, tone = STATUS_LABEL[st.status] if st.status in STATUS_LABEL else ("Waiting for backend", "info")
        if st.cancelling and st.active:
            label_text, tone = "Cancelling…", "warning"
        self.state_pill.set(label_text, tone)
        self.dot.set_color(TONES.get(tone, MUTED))
        done = sum(1 for s in st.steps if s.status == "completed")
        self.m_steps[1].setText(f"{done} / {len(st.steps)}")
        counts = st.counts()
        self.m_ok[1].setText(str(counts.get("Verified success", 0)))
        for i, node in enumerate(self.stage_nodes):
            node.set_state(self._stage_state(st, i))
        if counts:
            self.verif.setText("\n".join(f"{n} × {name}" for name, n in counts.items()))
        else:
            self.verif.setText("No results reported yet.")

    def set_elapsed(self, seconds: int) -> None:
        self.m_time[1].setText(f"{seconds // 60:02d}:{seconds % 60:02d}")

    @staticmethod
    def _stage_state(st: TaskState, index: int) -> str:
        steps = [s for s in st.steps if s.stage == index]
        if not steps:
            return "running" if (index == 0 and st.status == "planning") else "pending"
        states = [node_state_for(s) for s in steps]
        for wanted in ("failed", "waiting", "running", "unknown"):
            if wanted in states:
                return wanted
        if all(s == "done" for s in states):
            return "done"
        if all(s in ("done", "unverified") for s in states):
            return "unverified"
        if "cancelled" in states:
            return "cancelled"
        return "pending"


class Drawer(QWidget):
    """Slide-out host for the activity panel: keeps the main view calm until you want it."""

    WIDTH = 316

    def __init__(self, inner: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.inner = inner
        inner.setParent(self)
        inner.hide()
        self.is_open = False
        self._w = Tween(self, 0.0, QEasingCurve.Type.InOutCubic)
        self._w.changed.connect(self._apply)
        self.setFixedWidth(0)

    def _apply(self, value: float) -> None:
        self.setFixedWidth(int(value))
        self.inner.setVisible(value > 2)
        self.inner.setGeometry(16, 0, 300, self.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.inner.setGeometry(16, 0, 300, self.height())

    def set_open(self, open_: bool) -> None:
        self.is_open = open_
        self._w.to(self.WIDTH if open_ else 0, 420)


class TopBar(QWidget):
    palette_clicked = Signal()
    drawer_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(48)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(10)
        self.crumb = QLabel()
        self.crumb.setStyleSheet("font-size:15px; background:transparent;")
        row.addWidget(self.crumb)
        row.addStretch(1)
        self.palette_btn = GhostButton("Commands   Ctrl K", "neutral", "search")
        self.palette_btn.clicked.connect(self.palette_clicked)
        row.addWidget(self.palette_btn)
        self.drawer_btn = GhostButton("Activity", "neutral", "pulse")
        self.drawer_btn.setToolTip("Show or hide the activity panel (Ctrl+Shift+A)")
        self.drawer_btn.clicked.connect(self.drawer_clicked)
        row.addWidget(self.drawer_btn)
        row.addWidget(Pill("Local session", "success", dot=True))
        self.set_page("New task")

    def set_page(self, name: str) -> None:
        self.crumb.setText(
            f"{em('Phanes', MUTED)}<span style='color:{FAINT}'>&nbsp;&nbsp;/&nbsp;&nbsp;</span>"
            f"<span style='color:{TEXT}; font-weight:600'>{name}</span>"
        )


class Sidebar(Glass):
    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, radius=22)
        self.setFixedWidth(236)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 20, 16, 16)
        root.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        brand.addWidget(LogoMark(40))
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label("Phanes", 19, TEXT, "700"))
        names.addWidget(label("AI-OS", 11, MUTED, "600"))
        brand.addLayout(names, 1)
        root.addLayout(brand)
        root.addSpacing(26)
        root.addWidget(label("WORKSPACE", 11, FAINT, "700"))
        root.addSpacing(4)
        self.items: dict[str, NavItem] = {}
        for key, text, icon_name, enabled, tip in (
            ("new", "New task", "plus", True, ""),
            ("workspace", "Open workspace", "folder", True, ""),
            ("recipes", "Starting points", "layers", True, ""),
            ("history", "Task history", "history", False, "Task history needs the storage module (Person 4)."),
        ):
            item = NavItem(text, icon_name)
            item.setEnabled(enabled)
            if tip:
                item.setToolTip(tip)
            item.clicked.connect(lambda _c=False, k=key: self.navigate.emit(k))
            self.items[key] = item
            root.addWidget(item)
        self.items["new"].set_active(True)
        root.addStretch(1)
        box = Glass(radius=14, tint=0.035)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(4)
        line = QHBoxLayout()
        line.addWidget(PulseDot(MINT))
        line.addWidget(label("Desktop · local", 13, TEXT, "600"))
        line.addStretch(1)
        bl.addLayout(line)
        bl.addWidget(label("Prototype build · early access", 11, MUTED))
        root.addWidget(box)


class FadeStack(QStackedWidget):
    def fade_to(self, index: int) -> None:
        if index == self.currentIndex():
            return
        page = self.widget(index)
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        self.setCurrentIndex(index)
        anim = QPropertyAnimation(effect, b"opacity", page)
        anim.setDuration(420)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def done() -> None:
            try:
                page.setGraphicsEffect(None)
            except RuntimeError:
                pass

        anim.finished.connect(done)
        anim.start()
        self._anim = anim


# =============================================================================
# 8. MAIN WINDOW
# =============================================================================
class MainWindow(QMainWindow):
    DRAWER_MIN_WIDTH = 1240

    def __init__(self, service: SimulatedTaskService):
        super().__init__()
        self.service = service
        self.state = TaskState()
        self.workspace: Path | None = None
        self.dialog: ApprovalDialog | None = None
        self._anims: list[QPropertyAnimation] = []

        self.setWindowTitle("Phanes AI-OS")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1040, 680)
        self.resize(1440, 880)

        self.backdrop = Aurora()
        self.setCentralWidget(self.backdrop)
        shell = QHBoxLayout(self.backdrop)
        shell.setContentsMargins(18, 18, 18, 18)
        shell.setSpacing(0)

        self.sidebar = Sidebar()
        shell.addWidget(self.sidebar)
        shell.addSpacing(16)

        center = QVBoxLayout()
        center.setSpacing(4)
        self.topbar = TopBar()
        center.addWidget(self.topbar)
        self.stack = FadeStack()
        self.home = HomePage()
        self.run = RunPage()
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.run)
        center.addWidget(self.stack, 1)
        shell.addLayout(center, 1)

        self.activity = ActivityPanel()
        self.drawer = Drawer(self.activity)
        shell.addWidget(self.drawer)

        self.palette = CommandPalette(self.backdrop)

        self.sidebar.navigate.connect(self._on_nav)
        self.topbar.palette_clicked.connect(self._toggle_palette)
        self.topbar.drawer_clicked.connect(self._toggle_drawer)
        self.home.pick_workspace.connect(self._pick_workspace)
        self.home.goal_submitted.connect(self._submit)
        self.home.mode_changed.connect(self.backdrop.set_trip)
        self.run.back_requested.connect(self._back_home)
        self.run.cancel_requested.connect(self._cancel)
        self.run.review_requested.connect(self._open_approval)
        self._unsubscribe = service.subscribe(self._on_service_event)
        for sequence, handler in (("Ctrl+K", self._toggle_palette), ("Ctrl+Shift+A", self._toggle_drawer), ("Ctrl+N", self._new_task)):
            QShortcut(QKeySequence(sequence), self).activated.connect(handler)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick_clock)

        self.activity.refresh(None)
        self._anims += self.home.entrance()
        self._anims.append(fade_in(self.sidebar, 0, 600))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "palette"):
            return
        self.palette.setGeometry(self.backdrop.rect())
        wide = self.width() >= self.DRAWER_MIN_WIDTH
        self.topbar.drawer_btn.setEnabled(wide)
        if not wide and self.drawer.is_open:
            self.drawer.set_open(False)

    # -- navigation / commands -----------------------------------------------------------
    def _show_page(self, index: int) -> None:
        self.stack.fade_to(index)
        self.topbar.set_page("New task" if index == 0 else "Running task")

    def _on_nav(self, key: str) -> None:
        if key == "new":
            self._new_task()
        elif key == "workspace":
            self._pick_workspace()
        elif key == "recipes":
            self._go_recipes()

    def _new_task(self) -> None:
        if self.state.active:
            return
        self._back_home()
        self.home.scroll_top()
        self.home.composer.edit.setFocus()

    def _go_recipes(self) -> None:
        if self.state.active:
            return
        self._back_home()
        QTimer.singleShot(80, self.home.scroll_to_recipes)

    def _toggle_drawer(self) -> None:
        if self.width() >= self.DRAWER_MIN_WIDTH:
            self.drawer.set_open(not self.drawer.is_open)

    def _toggle_palette(self) -> None:
        if self.palette.is_open():
            self.palette.close_palette()
            return
        entries = []
        if not self.state.active:
            entries += [
                ("New task", "Ctrl N", self._new_task),
                ("Select workspace…", "", self._pick_workspace),
                ("Jump to starting points", "", self._go_recipes),
            ]
            entries += [(f"Start: {title}", MODES[mode][0], lambda m=mode, g=goal: self._use_recipe(m, g)) for mode, _i, title, _d, goal in RECIPES]
        else:
            entries.append(("Cancel running task", "", self._cancel))
        entries.append(("Toggle activity panel", "Ctrl Shift A", self._toggle_drawer))
        self.palette.open(entries)

    def _use_recipe(self, mode: int, goal: str) -> None:
        self._back_home()
        self.home.use_recipe(mode, goal)

    def _pick_workspace(self) -> None:
        if self.state.active:
            return
        chosen = QFileDialog.getExistingDirectory(self, "Select a workspace folder", str(self.workspace or Path.home()))
        if chosen:
            self.workspace = Path(chosen)
            self.home.set_workspace(self.workspace)

    # -- task lifecycle ---------------------------------------------------------------------
    def _submit(self, goal: str) -> None:
        if self.workspace is None:
            self.home.composer.show_error("Select a workspace first.")
            return
        try:
            task_id = self.service.submit(goal, str(self.workspace))
        except Exception as exc:  # the service is external code: show, don't crash
            self.home.composer.show_error(f"Couldn't submit the task. {exc}")
            return
        self.state = TaskState(goal=goal, task_id=task_id, started=time.monotonic())
        self.run.reset(goal)
        self.run.append_log(f"Task accepted by the backend ({task_id}). Waiting for updates.")
        self.home.composer.clear()
        self._show_page(1)
        if self.width() >= self.DRAWER_MIN_WIDTH:
            self.drawer.set_open(True)
        self._clock.start()
        self._render()

    def _cancel(self) -> None:
        if not self.state.active or self.state.cancelling:
            return
        self.state.cancelling = True
        if self.state.task_id is not None:
            self.service.cancel(self.state.task_id)
        self.run.append_log("Cancellation requested. Waiting for the backend to confirm.", "warning")
        self._render()

    def _back_home(self) -> None:
        if self.state.active:
            return  # a running task keeps the run view; cancel it first
        if self.stack.currentIndex() == 0:
            return
        self.state = TaskState()
        self.activity.refresh(None)
        self._show_page(0)

    def _tick_clock(self) -> None:
        if self.state.active:
            self.activity.set_elapsed(int(time.monotonic() - self.state.started))
        else:
            self._clock.stop()

    # -- canonical shared-service event adapter -----------------------------------------------
    def _on_service_event(self, event: object) -> None:
        """Translate shared.service events into the view-model events used by this UI.

        The shared contract currently has no PlanCreated event. Steps are therefore
        added as StepStarted events arrive; a future agreed plan event can populate
        the full plan before execution without changing the UI's rendering code.
        """
        # The simulator still emits local view-model events. Pass them through so
        # the prototype remains usable while the real service is being wired in.
        if isinstance(event, (TaskStatusEv, PlanEv, StepStatusEv, ResultEv, ApprovalEv, LogEv)):
            self._on_event(event)
            return

        task_id = getattr(event, "task_id", None)
        if task_id is not None and self.state.task_id is not None and task_id != self.state.task_id:
            return

        if isinstance(event, TaskEvent):
            raw = getattr(event.status, "value", event.status)
            status = str(raw).lower()
            status_map = {
                "created": "pending", "planning": "planning", "ready": "running",
                "running": "running", "waiting_approval": "waiting_for_approval",
                "waiting_for_approval": "waiting_for_approval", "cancelling": "running",
                "succeeded": "completed", "success": "completed", "completed": "completed",
                "failed": "failed", "cancelled": "cancelled", "needs_attention": "unknown",
            }
            self._on_event(TaskStatusEv(status_map.get(status, "unknown"), getattr(event, "message", "")))
            return

        if isinstance(event, StepStartedEvent):
            step_id = str(getattr(event, "step_id", ""))
            tool = str(getattr(event, "tool", "unknown tool"))
            existing = self.state.step(step_id)
            if existing is None and step_id:
                self.state.steps.append(StepVM(step_id, tool.replace("_", " ").replace(".", " · "), tool, 2))
                self.run.rebuild(self.state.steps)
            self._on_event(StepStatusEv(step_id, "running"))
            return

        if isinstance(event, StepFinishedEvent):
            result = event.result
            raw_status = getattr(getattr(result, "status", None), "value", getattr(result, "status", "unknown"))
            raw_status = str(raw_status).lower()
            status_map = {"succeeded": "completed", "success": "completed", "failed": "failed",
                          "timed_out": "failed", "timed-out": "failed", "cancelled": "cancelled",
                          "unknown": "unknown", "completed": "completed"}
            vm = ResultVM(
                status=status_map.get(raw_status, "unknown"),
                success=bool(getattr(result, "success", False)),
                verified=bool(getattr(result, "verified", False)),
                details=(getattr(result, "verification_details", None)
                         or getattr(getattr(result, "error", None), "message", None)),
                output=str(getattr(result, "output", "") or ""),
            )
            self._on_event(ResultEv(str(getattr(result, "step_id", "")), vm))
            return

        if isinstance(event, ApprovalRequestedEvent):
            request = event.request
            approval_id = str(getattr(request, "approval_id", ""))
            step_id = str(getattr(request, "step_id", ""))
            tool = str(getattr(request, "tool", "action"))
            arguments = getattr(request, "arguments", {}) or {}
            summary = str(getattr(request, "reason", "") or getattr(request, "description", "")
                          or f"The backend requests approval for {tool}.")
            self._on_event(ApprovalEv(approval_id, step_id, tool, summary, dict(arguments)))
            return

        if isinstance(event, ApprovalResolvedEvent):
            approved = bool(getattr(event, "approved", False))
            message = getattr(event, "message", "") or ("Approval granted." if approved else "Approval rejected.")
            self._on_event(LogEv(message, "info" if approved else "warning"))
            return

        if isinstance(event, LogEvent):
            self._on_event(LogEv(str(getattr(event, "message", "")), str(getattr(event, "level", "info"))))
            return

        if isinstance(event, TaskFinishedEvent):
            raw = getattr(event.status, "value", event.status)
            status = str(raw).lower()
            status_map = {"succeeded": "completed", "success": "completed", "completed": "completed",
                          "failed": "failed", "cancelled": "cancelled", "needs_attention": "unknown"}
            self._on_event(TaskStatusEv(status_map.get(status, "unknown"), getattr(event, "summary", "")))
            return

        # Keep diagnostics visible for event types the shared contract adds later.
        if self.state.task_id is not None:
            self._on_event(LogEv(f"Ignored unsupported service event: {type(event).__name__}", "warning"))

    # -- events from the backend ---------------------------------------------------------------
    def _on_event(self, ev) -> None:
        st = self.state
        if st.task_id is None:
            return
        if isinstance(ev, TaskStatusEv):
            st.status, st.message = ev.status, ev.message
            text = STATUS_LABEL.get(ev.status, (ev.status, ""))[0]
            self.run.append_log(f"Task: {text}" + (f" — {ev.message}" if ev.message else ""), "warning" if ev.status in ("failed", "cancelled") else "info")
            if ev.status != "waiting_for_approval":
                st.approval = None
            if ev.status in TERMINAL:
                st.cancelling = False
        elif isinstance(ev, PlanEv):
            st.steps = ev.steps
            self.run.rebuild(st.steps)
            self.run.append_log(f"Plan received with {len(st.steps)} steps.")
        elif isinstance(ev, StepStatusEv):
            step = st.step(ev.step_id)
            if step is None:
                self.run.append_log(f"Ignored an update for unknown step {ev.step_id}.", "warning")
            else:
                step.status, step.message = ev.status, ev.message
                self.run.update_step(step)
        elif isinstance(ev, ResultEv):
            step = st.step(ev.step_id)
            if step is None:
                self.run.append_log(f"Ignored a result for unknown step {ev.step_id}.", "warning")
            else:
                step.result, step.status = ev.result, ev.result.status
                self.run.update_step(step)
                outcome, tone = classify(ev.result)
                self.run.append_log(f"{step.title}: {outcome}", "error" if tone == "danger" else "info")
        elif isinstance(ev, ApprovalEv):
            st.approval = ev
            self.run.append_log(f"Approval requested for {ev.tool}.", "warning")
            self._open_approval()
        elif isinstance(ev, LogEv):
            self.run.append_log(ev.text, ev.level)
        self._render()

    def _open_approval(self) -> None:
        ev = self.state.approval
        if ev is None or (self.dialog is not None and self.dialog.isVisible()):
            return
        self.dialog = ApprovalDialog(ev, self)
        self.dialog.decided.connect(lambda ok, a=ev.approval_id: self._on_decision(a, ok))
        self.dialog.open()  # non-blocking

    def _on_decision(self, approval_id: str, approved: bool) -> None:
        self.run.append_log("Decision sent: " + ("approve" if approved else "reject") + ". Waiting for the backend to confirm.")
        try:
            approval = self.state.approval
            if approval is None or self.state.task_id is None:
                raise RuntimeError("The approval is no longer active.")
            self.service.resolve_approval(
                ApprovalDecision(
                    approval_id=approval_id,
                    task_id=self.state.task_id,
                    step_id=approval.step_id,
                    approved=approved,
                    decided_at=datetime.now(timezone.utc),
                )
            )
        except Exception as exc:
            self.run.append_log(f"Could not send your decision: {exc}", "error")
            self.run.show_banner("danger", "Decision not sent", str(exc), action=False)
            return
        self.state.approval = None
        self._render()

    # -- rendering -----------------------------------------------------------------------------------
    def _render(self) -> None:
        st = self.state
        label_text, tone = STATUS_LABEL[st.status] if st.status in STATUS_LABEL else ("Waiting for backend", "info")
        if st.cancelling and st.active:
            label_text, tone = "Cancelling…", "warning"
        self.run.status.set(label_text, tone)
        self.run.cancel.setVisible(st.active)
        self.run.cancel.setEnabled(not st.cancelling)
        self.run.back.setEnabled(not st.active)

        done = sum(1 for s in st.steps if s.status == "completed")
        total = len(st.steps)
        if total:
            parts = [f"{done} of {total} steps completed"]
            parts += [f"{n} {name.lower()}" for name, n in st.counts().items()]
            self.run.summary.setText("  ·  ".join(parts))
        else:
            self.run.summary.setText("Waiting for the plan…")
        self.run.bar.set_progress(done / total if total else 0.0, busy=st.active, indeterminate=st.active and total == 0)

        if st.approval is not None:
            self.run.show_banner("warning", "Waiting for your approval", st.approval.summary, action=True)
        elif st.status == "completed":
            self.run.show_banner("info", "The backend reported this task as completed", "Check each step's verification label: completed is not the same as verified.")
        elif st.status == "failed":
            self.run.show_banner("danger", "The backend reported this task as failed", st.message or "No details were provided.")
        elif st.status == "cancelled":
            self.run.show_banner("muted", "Task cancelled", st.message or "")
        else:
            self.run.hide_banner()
        self.activity.refresh(st)

    def closeEvent(self, event) -> None:
        self._clock.stop()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    base = QFont()
    base.setFamilies(FONT_FAMILIES)
    base.setPixelSize(14)
    app.setFont(base)
    app.setStyleSheet(
        f"QToolTip{{background:#141A27;color:{TEXT};border:1px solid {css('#FFFFFF', 0.12)};padding:6px 8px;border-radius:6px;}}"
        f"QScrollBar:vertical{{background:transparent;width:10px;margin:2px;}}"
        f"QScrollBar::handle:vertical{{background:{css('#FFFFFF', 0.14)};border-radius:4px;min-height:28px;}}"
        f"QScrollBar::handle:vertical:hover{{background:{css('#FFFFFF', 0.26)};}}"
        "QScrollBar::add-line,QScrollBar::sub-line{height:0;width:0;} QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}"
    )
    service = SimulatedTaskService()
    window = MainWindow(service)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
