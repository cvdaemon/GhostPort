from __future__ import annotations

import logging
import queue

from PySide6.QtCore import QObject, Signal

from ghostport.profiles.models import ServerProfile
from ghostport.ssh.session import ConnectionState

log = logging.getLogger("ghostport.sftp")


class SftpSession(QObject):
    """GUI facade; widgets never touch SftpWorker."""

    state_changed = Signal(object)
    listing_ready = Signal(str, object)
    stat_ready = Signal(str, str, object)
    op_finished = Signal(str, bool, str, object)
    error_occurred = Signal(object)
    cwd_changed = Signal(str)
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
        self._restart = False
        self.start_directory = profile.initial_directory or ""

    def attach_queue(self, cmd_queue: queue.Queue) -> None:
        self._queue = cmd_queue

    def bind_signals(self, worker_signals: QObject) -> None:
        worker_signals.listing_ready.connect(self.listing_ready)
        worker_signals.cwd_changed.connect(self.cwd_changed)
        worker_signals.stat_ready.connect(self.stat_ready)
        worker_signals.op_finished.connect(self.op_finished)
        worker_signals.state_changed.connect(self._on_state)
        worker_signals.error_occurred.connect(self.error_occurred)

    def _on_state(self, state: ConnectionState) -> None:
        self.state = state
        self.state_changed.emit(state)

    def listdir(self, path: str) -> None:
        from ghostport.sftp.connection import ListDir

        self._queue.put(ListDir(path))

    def mkdir(self, path: str, request_id: str) -> None:
        from ghostport.sftp.connection import MkdirPath

        self._queue.put(MkdirPath(path, request_id))

    def mkfile(self, path: str, request_id: str) -> None:
        from ghostport.sftp.connection import MkfilePath

        self._queue.put(MkfilePath(path, request_id))

    def rename(self, src: str, dst: str, request_id: str) -> None:
        from ghostport.sftp.connection import RenamePath

        self._queue.put(RenamePath(src, dst, request_id))

    def remove(self, path: str, *, recursive: bool, request_id: str) -> None:
        from ghostport.sftp.connection import RemovePath

        self._queue.put(RemovePath(path, recursive, request_id))

    def download(self, remote: str, local: str, *, is_dir: bool, request_id: str) -> None:
        from ghostport.sftp.connection import DownloadPath

        self._queue.put(DownloadPath(remote, local, is_dir, request_id))

    def stat(self, path: str, request_id: str) -> None:
        from ghostport.sftp.connection import StatPath

        self._queue.put(StatPath(path, request_id))

    def disconnect(self) -> None:
        from ghostport.sftp.connection import Shutdown

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
            from ghostport.sftp.connection import Shutdown

            self._queue.put(Shutdown())
            return
        self.reconnect_requested.emit()
