from __future__ import annotations

import logging
import os
import posixpath
import queue
import stat as pystat
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal
from paramiko.sftp_client import SFTPClient
from paramiko.transport import Transport
from shiboken6 import isValid

from ghostport.errors import HostKeyAborted
from ghostport.profiles.models import ServerProfile
from ghostport.security.credentials import CredentialStore
from ghostport.sftp.file_model import FileEntry, entry_from_attr
from ghostport.ssh.keepalive import apply_keepalive
from ghostport.ssh.known_hosts import HostKeyGate, KnownHostsStore
from ghostport.ssh.session import ConnectionState
from ghostport.ssh.transport import open_authenticated_transport, translate_connect_error

log = logging.getLogger("ghostport.sftp")


@dataclass(frozen=True)
class ListDir:
    path: str


@dataclass(frozen=True)
class StatPath:
    path: str
    request_id: str


@dataclass(frozen=True)
class Shutdown:
    pass


@dataclass(frozen=True)
class RenamePath:
    src: str
    dst: str
    request_id: str


@dataclass(frozen=True)
class RemovePath:
    path: str
    recursive: bool
    request_id: str


@dataclass(frozen=True)
class DownloadPath:
    remote: str
    local: str
    is_dir: bool
    request_id: str


@dataclass(frozen=True)
class MkdirPath:
    path: str
    request_id: str


@dataclass(frozen=True)
class MkfilePath:
    path: str
    request_id: str


def resolve_remote_path(path: str, *, home: str, cwd: str) -> str:
    """Turn a shell path (`~`, `~/a`, relative, absolute) into a remote absolute path.

    SFTP realpath does not expand `~`. `home` is the login directory (`normalize(".")`).
    """
    raw = (path or "").strip().replace("\\", "/")
    base_home = (home or cwd or "/").strip() or "/"
    base_cwd = (cwd or base_home or "/").strip() or "/"
    if raw in ("", "."):
        return base_cwd
    if raw == "~":
        return base_home
    if raw.startswith("~/"):
        rest = raw[2:].lstrip("/")
        root = base_home.rstrip("/") or "/"
        if root == "/":
            return posixpath.normpath("/" + rest)
        return posixpath.normpath(f"{root}/{rest}")
    if raw.startswith("/"):
        return posixpath.normpath(raw) or "/"
    root = base_cwd.rstrip("/") or "/"
    if root == "/":
        return posixpath.normpath("/" + raw)
    return posixpath.normpath(f"{root}/{raw}")


class SftpWorkerSignals(QObject):
    listing_ready = Signal(str, object)
    cwd_changed = Signal(str)
    stat_ready = Signal(str, str, object)
    op_finished = Signal(str, bool, str, object)
    state_changed = Signal(object)
    error_occurred = Signal(object)
    host_key_prompt = Signal(object)
    closed = Signal()


class NativeSftpThread(QThread):
    def __init__(self, worker: SftpWorker, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker = worker

    def run(self) -> None:
        self._worker.run()


class SftpWorker:
    def __init__(
        self,
        profile: ServerProfile,
        credentials: CredentialStore,
        known_hosts: KnownHostsStore,
        host_key_gate: HostKeyGate,
        cmd_queue: queue.Queue,
        signals: SftpWorkerSignals,
        *,
        timeout_sec: float,
        initial_directory: str | None,
    ) -> None:
        self._profile = profile
        self._credentials = credentials
        self._known_hosts = known_hosts
        self._gate = host_key_gate
        self._queue = cmd_queue
        self.signals = signals
        self._timeout = timeout_sec
        self._initial = initial_directory
        self._transport: Transport | None = None
        self._sftp: SFTPClient | None = None
        self._shutdown = False
        self._cwd = "/"
        self._home = "/"

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
            sftp = SFTPClient.from_transport(transport)
            if sftp is None:
                raise RuntimeError("SFTP client was not created")
            self._transport = transport
            self._sftp = sftp
            home = sftp.normalize(".")
            self._home = home or "/"
            self._cwd = self._home
            start = resolve_remote_path(self._initial or ".", home=self._home, cwd=self._home)
            try:
                self._cwd = sftp.normalize(start)
            except OSError:
                log.info("SFTP start directory %s unavailable; using %s", start, self._home)
                self._cwd = self._home
            else:
                log.info("SFTP opened %s at %s", self._profile.name, self._cwd)
            self._emit("state_changed", ConnectionState.CONNECTED)
            self._list(self._cwd)
            self._loop()
        except HostKeyAborted:
            failed = True
            log.info("SFTP host key rejected for %s", self._profile.name)
            self._emit("state_changed", ConnectionState.DISCONNECTED)
        except Exception as exc:
            failed = True
            err = translate_connect_error(exc, self._profile)
            log.info("SFTP connect failed for %s: %s", self._profile.name, err.reason)
            self._emit("error_occurred", err)
            self._emit("state_changed", ConnectionState.FAILED)
        finally:
            self._close()
            if not failed:
                self._emit("state_changed", ConnectionState.DISCONNECTED)
            self._emit("closed")

    def _loop(self) -> None:
        while not self._shutdown:
            try:
                cmd = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self._apply(cmd)

    def _list(self, path: str) -> None:
        sftp = self._sftp
        if sftp is None:
            return
        target = resolve_remote_path(path or self._cwd, home=self._home or self._cwd, cwd=self._cwd)
        try:
            attrs = sftp.listdir_attr(target)
            entries: list[FileEntry] = []
            for attr in attrs:
                entry = entry_from_attr(target, attr)
                if entry is not None:
                    entries.append(entry)
            self._cwd = target
            self._emit("cwd_changed", target)
            self._emit("listing_ready", target, entries)
        except Exception as exc:
            err = translate_connect_error(exc, self._profile)
            err.reason = f"Could not list {target}: {exc}"
            self._emit("error_occurred", err)

    def _stat(self, path: str, request_id: str) -> None:
        sftp = self._sftp
        if sftp is None:
            self._emit("stat_ready", request_id, path, None)
            return
        try:
            attr = sftp.stat(path)
            parent = posixpath.dirname(path.rstrip("/")) or "/"
            name = posixpath.basename(path.rstrip("/")) or path
            attr.filename = name
            entry = entry_from_attr(parent, attr)
            self._emit("stat_ready", request_id, path, entry)
        except OSError:
            self._emit("stat_ready", request_id, path, None)

    def _apply(self, cmd: object) -> None:
        if isinstance(cmd, Shutdown):
            self._shutdown = True
            return
        if isinstance(cmd, ListDir):
            self._list(cmd.path)
            return
        if isinstance(cmd, StatPath):
            self._stat(cmd.path, cmd.request_id)
            return
        if isinstance(cmd, RenamePath):
            self._rename(cmd)
            return
        if isinstance(cmd, RemovePath):
            self._remove(cmd)
            return
        if isinstance(cmd, DownloadPath):
            self._download(cmd)
            return
        if isinstance(cmd, MkdirPath):
            self._mkdir(cmd)
            return
        if isinstance(cmd, MkfilePath):
            self._mkfile(cmd)

    def _mkdir(self, cmd: MkdirPath) -> None:
        sftp = self._sftp
        if sftp is None:
            self._emit("op_finished", cmd.request_id, False, "Not connected", None)
            return
        try:
            sftp.mkdir(cmd.path)
            self._emit("op_finished", cmd.request_id, True, "Folder created", cmd.path)
            self._list(self._cwd)
        except Exception as exc:
            self._emit("op_finished", cmd.request_id, False, str(exc), None)

    def _mkfile(self, cmd: MkfilePath) -> None:
        sftp = self._sftp
        if sftp is None:
            self._emit("op_finished", cmd.request_id, False, "Not connected", None)
            return
        try:
            try:
                sftp.stat(cmd.path)
            except OSError:
                pass
            else:
                raise FileExistsError(f"{cmd.path} already exists")
            with sftp.file(cmd.path, "w"):
                pass
            self._emit("op_finished", cmd.request_id, True, "File created", cmd.path)
            self._list(self._cwd)
        except Exception as exc:
            self._emit("op_finished", cmd.request_id, False, str(exc), None)

    def _rename(self, cmd: RenamePath) -> None:
        sftp = self._sftp
        if sftp is None:
            self._emit("op_finished", cmd.request_id, False, "Not connected", None)
            return
        try:
            sftp.rename(cmd.src, cmd.dst)
            self._emit("op_finished", cmd.request_id, True, "Renamed", cmd.dst)
            self._list(self._cwd)
        except Exception as exc:
            self._emit("op_finished", cmd.request_id, False, str(exc), None)

    def _remove(self, cmd: RemovePath) -> None:
        sftp = self._sftp
        if sftp is None:
            self._emit("op_finished", cmd.request_id, False, "Not connected", None)
            return
        try:
            self._rm(sftp, cmd.path, recursive=cmd.recursive)
            self._emit("op_finished", cmd.request_id, True, "Deleted", cmd.path)
            self._list(self._cwd)
        except Exception as exc:
            self._emit("op_finished", cmd.request_id, False, str(exc), None)

    def _rm(self, sftp: SFTPClient, path: str, *, recursive: bool) -> None:
        try:
            attr = sftp.stat(path)
            is_dir = bool(attr.st_mode and pystat.S_ISDIR(attr.st_mode))
        except OSError:
            is_dir = False
        if is_dir:
            if not recursive:
                sftp.rmdir(path)
                return
            for child in sftp.listdir_attr(path):
                name = child.filename or ""
                if name in (".", ".."):
                    continue
                child_path = path.rstrip("/") + "/" + name if path != "/" else "/" + name
                self._rm(sftp, child_path, recursive=True)
            sftp.rmdir(path)
            return
        sftp.remove(path)

    def _download(self, cmd: DownloadPath) -> None:
        sftp = self._sftp
        if sftp is None:
            self._emit("op_finished", cmd.request_id, False, "Not connected", None)
            return
        try:
            if cmd.is_dir:
                self._download_dir(sftp, cmd.remote, cmd.local)
            else:
                os.makedirs(os.path.dirname(cmd.local) or ".", exist_ok=True)
                sftp.get(cmd.remote, cmd.local)
            self._emit("op_finished", cmd.request_id, True, "Downloaded", cmd.local)
        except Exception as exc:
            self._emit("op_finished", cmd.request_id, False, str(exc), None)

    def _download_dir(self, sftp: SFTPClient, remote: str, local: str) -> None:
        os.makedirs(local, exist_ok=True)
        for attr in sftp.listdir_attr(remote):
            name = attr.filename or ""
            if name in (".", ".."):
                continue
            rpath = remote.rstrip("/") + "/" + name if remote != "/" else "/" + name
            lpath = os.path.join(local, name)
            is_dir = bool(attr.st_mode and pystat.S_ISDIR(attr.st_mode))
            if is_dir:
                self._download_dir(sftp, rpath, lpath)
            else:
                sftp.get(rpath, lpath)

    def _close(self) -> None:
        sftp = self._sftp
        transport = self._transport
        self._sftp = None
        self._transport = None
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                log.debug("sftp.close failed", exc_info=True)
        if transport is not None:
            try:
                transport.close()
            except Exception:
                log.debug("sftp transport.close failed", exc_info=True)
