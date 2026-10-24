from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal


def idle_timeout_ms(minutes: int) -> int:
    """Milliseconds for an idle closer. 0 or negative minutes means never."""
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    return value * 60 * 1000


class IdleCloser(QObject):
    """Single-shot timer. `touch()` while armed postpones close."""

    idle = Signal()

    def __init__(
        self,
        minutes: int,
        parent: QObject | None = None,
        *,
        interval_ms: int | None = None,
    ) -> None:
        super().__init__(parent)
        if interval_ms is None:
            self._ms = idle_timeout_ms(minutes)
        else:
            self._ms = max(0, int(interval_ms))
        self._armed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.idle.emit)

    def set_armed(self, armed: bool) -> None:
        self._armed = bool(armed) and self._ms > 0
        if self._armed:
            self.touch()
        else:
            self._timer.stop()

    def touch(self) -> None:
        if not self._armed or self._ms <= 0:
            return
        self._timer.start(self._ms)

    def is_active(self) -> bool:
        return self._timer.isActive()
