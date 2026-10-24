from __future__ import annotations

import logging
import queue
import threading
from collections import defaultdict

from PySide6.QtCore import QObject, Signal

from ghostport.config.paths import AppPaths
from ghostport.config.settings import SettingsStore
from ghostport.profiles.manager import ProfileManager
from ghostport.profiles.models import ServerProfile
from ghostport.security.credentials import CredentialStore
from ghostport.sftp.connection import NativeSftpThread, SftpWorker, SftpWorkerSignals
from ghostport.sftp.session import SftpSession
from ghostport.ssh.connection import NativeWorkerThread, SshWorker, SshWorkerSignals
from ghostport.ssh.known_hosts import HostKeyDecision, HostKeyGate, HostKeyPrompt, KnownHostsStore
from ghostport.ssh.session import ConnectionState, SshSession

log = logging.getLogger("ghostport.ssh")


class ConnectionService(QObject):
    """GUI-thread factory. Host-key dialogs are shown only by MainWindow."""

    host_key_decision_needed = Signal(object)

    def __init__(
        self,
        profiles: ProfileManager,
        credentials: CredentialStore,
        paths: AppPaths,
        settings: SettingsStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._profiles = profiles
        self._credentials = credentials
        self._paths = paths
        self._settings = settings
        self._known_hosts = KnownHostsStore(paths.known_hosts_file)
        self._gate = HostKeyGate()
        self._hk_lock = threading.Lock()
        self._waiters: dict[tuple[str, int], list[str]] = defaultdict(list)
        self._pending_dialog: set[tuple[str, int]] = set()
        self._sessions: dict[int, _LiveSsh] = {}
        self._sftp: dict[int, _LiveSftp] = {}

    @property
    def known_hosts(self) -> KnownHostsStore:
        return self._known_hosts

    @property
    def host_key_gate(self) -> HostKeyGate:
        return self._gate

    def start_ssh(
        self,
        profile_id: str,
        *,
        initial_directory: str | None = None,
        parent: QObject | None = None,
        cols: int = 80,
        rows: int = 24,
    ) -> SshSession:
        profile = self._profiles.get(profile_id)
        cmd_queue: queue.Queue = queue.Queue()
        session = SshSession(profile, cmd_queue, parent=parent)
        signals = SshWorkerSignals(self)
        session.bind_signals(signals)
        signals.host_key_prompt.connect(self._on_host_key_prompt)
        timeout = float(profile.timeout_sec or self._settings.get().connections.timeout_sec)
        directory = initial_directory if initial_directory is not None else profile.initial_directory
        worker = SshWorker(
            profile,
            self._credentials,
            self._known_hosts,
            self._gate,
            cmd_queue,
            signals,
            timeout_sec=timeout,
            cols=cols,
            rows=rows,
            initial_directory=directory or None,
        )
        thread = NativeWorkerThread(worker, parent=self)
        live = _LiveSsh(session=session, thread=thread, queue=cmd_queue, signals=signals)
        self._sessions[id(session)] = live
        thread.finished.connect(lambda sid=id(session): self._on_thread_finished(sid))
        session.reconnect_requested.connect(lambda s=session: self._restart_ssh(s))
        thread.start()
        return session

    def _restart_ssh(self, session: SshSession) -> None:
        live = self._sessions.get(id(session))
        if live is None:
            return
        if live.thread is not None and live.thread.isRunning():
            return
        session._restart = False
        profile = self._profiles.get(session.profile_id)
        session.profile = profile
        cmd_queue: queue.Queue = queue.Queue()
        session.attach_queue(cmd_queue)
        live.queue = cmd_queue
        timeout = float(profile.timeout_sec or self._settings.get().connections.timeout_sec)
        worker = SshWorker(
            profile,
            self._credentials,
            self._known_hosts,
            self._gate,
            cmd_queue,
            live.signals,
            timeout_sec=timeout,
            cols=80,
            rows=24,
            initial_directory=profile.initial_directory or None,
        )
        thread = NativeWorkerThread(worker, parent=self)
        live.thread = thread
        thread.finished.connect(lambda sid=id(session): self._on_thread_finished(sid))
        session.state = ConnectionState.CONNECTING
        session.state_changed.emit(session.state)
        thread.start()

    def _on_thread_finished(self, session_id: int) -> None:
        live = self._sessions.get(session_id)
        if live is None:
            return
        if live.session._restart:
            self._restart_ssh(live.session)
            return
        live.session.thread_finished.emit()

    def _on_host_key_prompt(self, prompt: HostKeyPrompt) -> None:
        key = (prompt.host, prompt.port)
        with self._hk_lock:
            self._waiters[key].append(prompt.prompt_id)
            already = key in self._pending_dialog
            if not already:
                self._pending_dialog.add(key)
        if already:
            return
        self.host_key_decision_needed.emit(prompt)

    def provide_host_key_decision(self, prompt_id: str, decision: HostKeyDecision) -> None:
        with self._hk_lock:
            found_key: tuple[str, int] | None = None
            ids: list[str] = []
            for key, waiters in self._waiters.items():
                if prompt_id in waiters:
                    found_key = key
                    ids = list(waiters)
                    break
            if found_key is not None:
                self._waiters.pop(found_key, None)
                self._pending_dialog.discard(found_key)
        if not ids:
            ids = [prompt_id]
        for pid in ids:
            self._gate.resolve(pid, decision)

    def start_sftp(
        self,
        profile_id: str,
        parent: QObject | None = None,
        *,
        initial_directory: str | None = None,
    ) -> SftpSession:
        profile = self._profiles.get(profile_id)
        cmd_queue: queue.Queue = queue.Queue()
        session = SftpSession(profile, cmd_queue, parent=parent)
        start = (initial_directory if initial_directory is not None else profile.initial_directory) or None
        session.start_directory = start or ""
        signals = SftpWorkerSignals(self)
        session.bind_signals(signals)
        signals.host_key_prompt.connect(self._on_host_key_prompt)
        timeout = float(profile.timeout_sec or self._settings.get().connections.timeout_sec)
        worker = SftpWorker(
            profile,
            self._credentials,
            self._known_hosts,
            self._gate,
            cmd_queue,
            signals,
            timeout_sec=timeout,
            initial_directory=start,
        )
        thread = NativeSftpThread(worker, parent=self)
        live = _LiveSftp(session=session, thread=thread, queue=cmd_queue, signals=signals)
        self._sftp[id(session)] = live
        thread.finished.connect(lambda sid=id(session): self._on_sftp_thread_finished(sid))
        session.reconnect_requested.connect(lambda s=session: self._restart_sftp(s))
        thread.start()
        return session

    def _restart_sftp(self, session: SftpSession) -> None:
        live = self._sftp.get(id(session))
        if live is None:
            return
        if live.thread is not None and live.thread.isRunning():
            return
        session._restart = False
        profile = self._profiles.get(session.profile_id)
        session.profile = profile
        cmd_queue: queue.Queue = queue.Queue()
        session.attach_queue(cmd_queue)
        live.queue = cmd_queue
        timeout = float(profile.timeout_sec or self._settings.get().connections.timeout_sec)
        worker = SftpWorker(
            profile,
            self._credentials,
            self._known_hosts,
            self._gate,
            cmd_queue,
            live.signals,
            timeout_sec=timeout,
            initial_directory=session.start_directory or profile.initial_directory or None,
        )
        thread = NativeSftpThread(worker, parent=self)
        live.thread = thread
        thread.finished.connect(lambda sid=id(session): self._on_sftp_thread_finished(sid))
        session.state = ConnectionState.CONNECTING
        session.state_changed.emit(session.state)
        thread.start()

    def _on_sftp_thread_finished(self, session_id: int) -> None:
        live = self._sftp.get(session_id)
        if live is None:
            return
        if live.session._restart:
            self._restart_sftp(live.session)
            return
        live.session.thread_finished.emit()


class _LiveSsh:
    def __init__(
        self,
        session: SshSession,
        thread: NativeWorkerThread,
        queue: queue.Queue,
        signals: SshWorkerSignals,
    ) -> None:
        self.session = session
        self.thread = thread
        self.queue = queue
        self.signals = signals


class _LiveSftp:
    def __init__(
        self,
        session: SftpSession,
        thread: NativeSftpThread,
        queue: queue.Queue,
        signals: SftpWorkerSignals,
    ) -> None:
        self.session = session
        self.thread = thread
        self.queue = queue
        self.signals = signals
