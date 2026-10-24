from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


class ConnectionState(str, Enum):
    CONNECTED = "connected"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"
    SAVED = "saved"


_COLORS = {
    ConnectionState.CONNECTED: QColor("#3d9b94"),
    ConnectionState.CONNECTING: QColor("#c9a227"),
    ConnectionState.DISCONNECTED: QColor("#6b6b6b"),
    ConnectionState.SAVED: QColor("#3d9b94"),
}


class StatusDot(QWidget):
    def __init__(self, state: ConnectionState = ConnectionState.SAVED, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self.setFixedSize(QSize(10, 10))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_state(self, state: ConnectionState) -> None:
        self._state = state
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(_COLORS[self._state])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, 8, 8)
        painter.end()
