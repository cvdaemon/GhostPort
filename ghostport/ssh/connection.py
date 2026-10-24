from __future__ import annotations

import logging
import queue
import shlex
import socket
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal
from shiboken6 import isValid
from paramiko.channel import Channel
from paramiko.transport import Transport

from ghostport.errors import HostKeyAborted
from ghostport.profiles.models import ServerProfile
from ghostport.security.credentials import CredentialStore
from ghostport.ssh.keepalive import apply_keepalive
from ghostport.ssh.known_hosts import HostKeyGate, HostKeyPrompt, KnownHostsStore
from ghostport.ssh.session import ConnectionState
from ghostport.ssh.transport import open_authenticated_transport, translate_connect_error

log = logging.getLogger("ghostport.ssh")

MAX_INFLIGHT_BYTES = 256 * 1024
RECV_SIZE = 32768
IDLE_WAIT = 0.002


@dataclass(frozen=True)
class Stdin:
    data: bytes


@dataclass(frozen=True)
class Resize:
    cols: int
    rows: int
    width_px: int
    height_px: int
    gen: int


@dataclass(frozen=True)
class Ack:
    n: int


@dataclass(frozen=True)
class Shutdown:
    pass


class SshWorkerSignals(QObject):
    """Parent = SshSession (GUI affinity). Emitted from the native thread."""

    bytes_received = Signal(bytes)
    state_changed = Signal(object)
    error_occurred = Signal(object)
    host_key_prompt = Signal(object)
    ready = Signal()
    closed = Signal()


class NativeWorkerThread(QThread):
    """run() calls worker.run() and does not exec()."""

    def __init__(self, worker: SshWorker, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker = worker

    def run(self) -> None:  # noqa: D401
        self._worker.run()


class SshWorker:
    """Plain object. QThread.run() calls worker.run() and does NOT exec()."""

    def __init__(
        self,
        profile: ServerProfile,
        credentials: CredentialStore,
        known_hosts: KnownHostsStore,
        host_key_gate: HostKeyGate,
        cmd_queue: queue.Queue,
        signals: SshWorkerSignals,
        *,
        timeout_sec: float,
        cols: int,
        rows: int,
        initial_directory: str | None,
    ) -> None:
        self._profile = profile
        self._credentials = credentials
        self._known_hosts = known_hosts
        self._gate = host_key_gate
        self._queue = cmd_queue
        self.signals = signals
        self._timeout = timeout_sec
        self._cols = cols
        self._rows = rows
        self._initial_directory = initial_directory
        self._transport: Transport | None = None
        self._channel: Channel | None = None
        self._shutdown = False
        self._resize_gen = 0
        self._unacked = 0

    def _emit(self, name: str, *args) -> None:
        src = self.signals
        if src is None or not isValid(src):
            return
        try:
            getattr(src, name).emit(*args)
        except RuntimeError:
            return

    def run(self) -> None:
        failed = False
        try:
            self._emit("state_changed", ConnectionState.CONNECTING)
            transport = open_authenticated_transport(
                self._profile,
                self._credentials,
                self._known_hosts,
                timeout_sec=self._timeout,
                host_key_gate=self._gate,
                emit_prompt=lambda prompt: self._emit("host_key_prompt", prompt),
            )
            apply_keepalive(transport, self._profile)
            channel = transport.open_session()
            term = self._profile.term_type or "xterm-256color"
            channel.get_pty(term=term, width=self._cols, height=self._rows)
            channel.invoke_shell()
            self._transport = transport
            self._channel = channel
            self._emit("state_changed", ConnectionState.CONNECTED)
            self._emit("ready")
            if self._initial_directory:
                payload = f"cd {shlex.quote(self._initial_directory)}\r".encode("utf-8")
                self._queue.put(Stdin(payload))
            self._read_loop()
        except HostKeyAborted:
            log.info("Host key rejected for %s", self._profile.name)
            failed = True
            self._emit("state_changed", ConnectionState.DISCONNECTED)
        except Exception as exc:
            failed = True
            err = translate_connect_error(exc, self._profile)
            log.info(
                "Connect failed for %s: %s",
                self._profile.name,
                err.reason or err,
            )
            self._emit("error_occurred", err)
            self._emit("state_changed", ConnectionState.FAILED)
        finally:
            self._close_channel_and_transport()
            if not failed:
                self._emit("state_changed", ConnectionState.DISCONNECTED)
            self._emit("closed")

    def _read_loop(self) -> None:
        channel = self._channel
        assert channel is not None
        # Keep send blocking. Only recv when recv_ready() so keys are not
        # stuck behind a socket timeout.
        channel.settimeout(None)
        while True:
            self._drain_cmds(timeout=0)
            if self._shutdown:
                break
            if channel.closed or channel.eof_received or channel.exit_status_ready():
                try:
                    leftover = channel.recv(RECV_SIZE)
                except (socket.timeout, OSError):
                    leftover = b""
                if leftover:
                    self._unacked += len(leftover)
                    self._emit("bytes_received", leftover)
                    continue
                break
            if self._unacked >= MAX_INFLIGHT_BYTES:
                self._drain_cmds(timeout=IDLE_WAIT)
                continue
            if channel.recv_ready():
                try:
                    data = channel.recv(RECV_SIZE)
                except socket.timeout:
                    continue
                except OSError as exc:
                    err = translate_connect_error(exc, self._profile)
                    self._emit("error_occurred", err)
                    break
                if not data:
                    break
                self._unacked += len(data)
                self._emit("bytes_received", data)
                continue
            try:
                cmd = self._queue.get(timeout=IDLE_WAIT)
            except queue.Empty:
                continue
            self._apply_cmd(cmd)

    def _drain_cmds(self, timeout: float = 0) -> None:
        while True:
            try:
                if timeout > 0:
                    cmd = self._queue.get(timeout=timeout)
                    timeout = 0
                else:
                    cmd = self._queue.get_nowait()
            except queue.Empty:
                return
            self._apply_cmd(cmd)

    def _apply_cmd(self, cmd: object) -> None:
        if isinstance(cmd, Shutdown):
            self._shutdown = True
            return
        if isinstance(cmd, Ack):
            self._unacked = max(0, self._unacked - cmd.n)
            return
        if isinstance(cmd, Resize):
            if cmd.gen < self._resize_gen:
                return
            self._resize_gen = cmd.gen
            self._cols = cmd.cols
            self._rows = cmd.rows
            channel = self._channel
            if channel is not None and not channel.closed:
                try:
                    channel.resize_pty(
                        width=cmd.cols,
                        height=cmd.rows,
                        width_pixels=cmd.width_px,
                        height_pixels=cmd.height_px,
                    )
                except Exception as exc:
                    log.debug("resize_pty failed: %s", exc)
            return
        if isinstance(cmd, Stdin):
            channel = self._channel
            if channel is None or channel.closed:
                return
            try:
                channel.sendall(cmd.data)
            except Exception as exc:
                log.debug("sendall failed: %s", exc)
                self._shutdown = True

    def _close_channel_and_transport(self) -> None:
        channel = self._channel
        transport = self._transport
        self._channel = None
        self._transport = None
        if channel is not None:
            try:
                channel.close()
            except Exception:
                log.debug("channel.close failed", exc_info=True)
        if transport is not None:
            try:
                transport.close()
            except Exception:
                log.debug("transport.close failed", exc_info=True)
