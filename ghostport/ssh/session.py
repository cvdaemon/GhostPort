from __future__ import annotations

import logging
import queue
from enum import Enum

from PySide6.QtCore import QObject, Signal

from ghostport.profiles.models import ServerProfile

log = logging.getLogger("ghostport.ssh")


class ConnectionState(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class SshSession(QObject):
    """GUI thread; the only SSH object UI binds to."""

    bytes_received = Signal(bytes)
    state_changed = Signal(object)
    error_occurred = Signal(object)
    ready = Signal()
    closed = Signal()
    thread_finished = Signal()
    reconnect_requested = Signal()

    def __init__(
        self,
        profile: ServerProfile,
        cmd_queue: queue.Queue,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.profile_id = profile.id
        self.profile = profile
        self.state = ConnectionState.IDLE
        self._queue = cmd_queue
        self._resize_gen = 0
        self._restart = False

    def attach_queue(self, cmd_queue: queue.Queue) -> None:
        self._queue = cmd_queue

    def bind_signals(self, worker_signals: QObject) -> None:
        worker_signals.bytes_received.connect(self.bytes_received)
        worker_signals.state_changed.connect(self._on_state)
        worker_signals.error_occurred.connect(self.error_occurred)
        worker_signals.ready.connect(self.ready)
        worker_signals.closed.connect(self.closed)

    def _on_state(self, state: ConnectionState) -> None:
        self.state = state
        self.state_changed.emit(state)

    def send_stdin(self, data: bytes) -> None:
        from ghostport.ssh.connection import Stdin

        if not data:
            return
        self._queue.put(Stdin(data))

    def resize_pty(self, cols: int, rows: int, width_px: int, height_px: int) -> None:
        from ghostport.ssh.connection import Resize

        self._resize_gen += 1
        self._queue.put(
            Resize(
                cols=cols,
                rows=rows,
                width_px=width_px,
                height_px=height_px,
                gen=self._resize_gen,
            )
        )

    def ack_bytes(self, n: int) -> None:
        from ghostport.ssh.connection import Ack

        if n <= 0:
            return
        self._queue.put(Ack(n))

    def disconnect(self) -> None:
        from ghostport.ssh.connection import Shutdown

        self._restart = False
        self._queue.put(Shutdown())

    def reconnect(self) -> None:
        self._restart = True
        if self.state in (
            ConnectionState.CONNECTING,
            ConnectionState.CONNECTED,
            ConnectionState.RECONNECTING,
        ):
            self.state = ConnectionState.RECONNECTING
            self.state_changed.emit(self.state)
            from ghostport.ssh.connection import Shutdown

            self._queue.put(Shutdown())
            return
        self.reconnect_requested.emit()
