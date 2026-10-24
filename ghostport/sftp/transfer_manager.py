from __future__ import annotations

import logging
import os
import posixpath
import stat as pystat
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from paramiko.sftp_client import SFTPClient
from paramiko.transport import Transport
from shiboken6 import isValid

from ghostport.config.settings import SettingsStore
from ghostport.errors import HostKeyAborted
from ghostport.profiles.manager import ProfileManager
from ghostport.security.credentials import CredentialStore
from ghostport.ssh.keepalive import apply_keepalive
from ghostport.ssh.known_hosts import HostKeyGate, KnownHostsStore
from ghostport.ssh.transport import open_authenticated_transport, translate_connect_error

log = logging.getLogger("ghostport.sftp")


class TransferDirection(str, Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"


class TransferStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransferCancelled(Exception):
    pass


@dataclass
class TransferJob:
    id: str
    profile_id: str
    direction: TransferDirection
    local_path: str
    remote_path: str
    is_dir: bool = False
    nbytes_total: int | None = None
    nbytes_done: int = 0
    status: TransferStatus = TransferStatus.QUEUED
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    speed_bps: float = 0.0
    display_name: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    pause_event: threading.Event = field(default_factory=threading.Event)

    def eta_seconds(self) -> float | None:
        if self.nbytes_total is None or self.nbytes_total <= 0 or self.speed_bps <= 1:
            return None
        remaining = max(0, self.nbytes_total - self.nbytes_done)
        return remaining / self.speed_bps


SPEED_SAMPLE_WINDOW = 100


def average_speed(samples: list[tuple[float, int]], *, min_span: float = 0.2) -> float | None:
    """Bytes/second across the retained samples (the last progress window, not one tick)."""
    if len(samples) < 2:
        return None
    start_t, start_b = samples[0]
    end_t, end_b = samples[-1]
    span = end_t - start_t
    if span < min_span:
        return None
    return max(0.0, (end_b - start_b) / span)


def note_speed_sample(samples: list[tuple[float, int]], when: float, done: int) -> float | None:
    samples.append((when, max(0, int(done))))
    extra = len(samples) - SPEED_SAMPLE_WINDOW
    if extra > 0:
        del samples[:extra]
    return average_speed(samples)


def format_bytes(n: int | float | None) -> str:
    if n is None:
        return "—"
    value = float(n)
    if value < 1024:
        return f"{int(value)} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    if value < 1024 * 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    return f"{value / (1024 * 1024 * 1024):.2f} GB"


def format_speed(bps: float) -> str:
    """Fixed-width MB/s so the speed column does not jump between KB/s and MB/s."""
    if bps <= 0:
        return "—"
    return f"{bps / (1024 * 1024):8.3f} MB/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class TransferWorkerSignals(QObject):
    progress = Signal(str, int, object)
    finished = Signal(str, bool, str)
    host_key_prompt = Signal(object)


class NativeTransferThread(QThread):
    def __init__(self, worker: TransferWorker, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker = worker

    def run(self) -> None:
        self._worker.run()


class TransferWorker:
    def __init__(
        self,
        job: TransferJob,
        profile,
        credentials: CredentialStore,
        known_hosts: KnownHostsStore,
        host_key_gate: HostKeyGate,
        signals: TransferWorkerSignals,
        *,
        timeout_sec: float,
    ) -> None:
        self._job = job
        self._profile = profile
        self._credentials = credentials
        self._known_hosts = known_hosts
        self._gate = host_key_gate
        self.signals = signals
        self._timeout = timeout_sec
        self._transport: Transport | None = None
        self._sftp: SFTPClient | None = None
        self._last_emit = 0.0

    def _emit(self, name: str, *args) -> None:
        src = self.signals
        if src is None or not isValid(src):
            return
        try:
            getattr(src, name).emit(*args)
        except RuntimeError:
            return

    def run(self) -> None:
        job = self._job
        try:
            if job.cancel_event.is_set():
                raise TransferCancelled()
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
            if job.direction is TransferDirection.DOWNLOAD:
                self._download(sftp, job)
            else:
                self._upload(sftp, job)
            self._emit("finished", job.id, True, "Done")
        except TransferCancelled:
            self._emit("finished", job.id, False, "Cancelled")
        except HostKeyAborted:
            self._emit("finished", job.id, False, "Host key rejected")
        except Exception as exc:
            err = translate_connect_error(exc, self._profile)
            log.info("Transfer failed job=%s: %s", job.id, err.reason)
            self._emit("finished", job.id, False, err.reason or str(exc))
        finally:
            self._close()

    def _progress(self, done: int, total: int | None, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < 0.12:
            return
        self._last_emit = now
        self._emit("progress", self._job.id, int(done), total)

    def _check(self) -> None:
        while self._job.pause_event.is_set() and not self._job.cancel_event.is_set():
            time.sleep(0.05)
        if self._job.cancel_event.is_set():
            raise TransferCancelled()

    def _callback(self, base: int, total: int | None):
        def cb(transferred: int, _file_total: int) -> None:
            self._check()
            grand = base + int(transferred)
            self._progress(grand, total)

        return cb

    def _download(self, sftp: SFTPClient, job: TransferJob) -> None:
        if job.is_dir:
            files: list[tuple[str, str, int]] = []
            self._collect_remote(sftp, job.remote_path, job.local_path, files)
            total = sum(size for _r, _l, size in files)
            self._progress(0, total, force=True)
            done = 0
            for remote, local, size in files:
                self._check()
                os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
                sftp.get(remote, local, callback=self._callback(done, total))
                done += size
            self._progress(done, total, force=True)
            return
        os.makedirs(os.path.dirname(job.local_path) or ".", exist_ok=True)
        try:
            attr = sftp.stat(job.remote_path)
            total = int(attr.st_size or 0)
        except OSError:
            total = job.nbytes_total
        sftp.get(job.remote_path, job.local_path, callback=self._callback(0, total))
        self._progress(total or 0, total, force=True)

    def _upload(self, sftp: SFTPClient, job: TransferJob) -> None:
        if job.is_dir:
            files: list[tuple[str, str, int]] = []
            self._collect_local(job.local_path, job.remote_path, files)
            total = sum(size for _l, _r, size in files)
            self._progress(0, total, force=True)
            done = 0
            for local, remote, size in files:
                self._check()
                parent = posixpath.dirname(remote.rstrip("/")) or "/"
                self._mkdir_p(sftp, parent)
                sftp.put(local, remote, callback=self._callback(done, total))
                done += size
            self._progress(done, total, force=True)
            return
        parent = posixpath.dirname(job.remote_path.rstrip("/")) or "/"
        self._mkdir_p(sftp, parent)
        total = None
        try:
            total = int(os.path.getsize(job.local_path))
        except OSError:
            total = job.nbytes_total
        sftp.put(job.local_path, job.remote_path, callback=self._callback(0, total))
        self._progress(total or 0, total, force=True)

    def _collect_remote(
        self, sftp: SFTPClient, remote: str, local: str, out: list[tuple[str, str, int]]
    ) -> None:
        self._check()
        os.makedirs(local, exist_ok=True)
        for attr in sftp.listdir_attr(remote):
            name = attr.filename or ""
            if name in (".", ".."):
                continue
            rpath = remote.rstrip("/") + "/" + name if remote != "/" else "/" + name
            lpath = os.path.join(local, name)
            is_dir = bool(attr.st_mode and pystat.S_ISDIR(attr.st_mode))
            if is_dir:
                self._collect_remote(sftp, rpath, lpath, out)
            else:
                out.append((rpath, lpath, int(attr.st_size or 0)))

    def _collect_local(self, local: str, remote: str, out: list[tuple[str, str, int]]) -> None:
        self._check()
        root = Path(local)
        if root.is_file():
            out.append((str(root), remote, int(root.stat().st_size)))
            return
        for dirpath, _dirnames, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            if rel == ".":
                rdir = remote
            else:
                rdir = remote.rstrip("/") + "/" + rel.replace("\\", "/")
            for name in filenames:
                self._check()
                lpath = os.path.join(dirpath, name)
                rpath = rdir.rstrip("/") + "/" + name if rdir != "/" else "/" + name
                try:
                    size = int(os.path.getsize(lpath))
                except OSError:
                    size = 0
                out.append((lpath, rpath, size))

    def _mkdir_p(self, sftp: SFTPClient, path: str) -> None:
        if path in ("", "/", "."):
            return
        parts: list[str] = []
        current = path
        while current not in ("", "/", "."):
            parts.append(current)
            current = posixpath.dirname(current.rstrip("/")) or "/"
            if current == parts[-1]:
                break
        for folder in reversed(parts):
            try:
                sftp.mkdir(folder)
            except OSError:
                continue

    def _close(self) -> None:
        sftp = self._sftp
        transport = self._transport
        self._sftp = None
        self._transport = None
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                log.debug("transfer sftp.close failed", exc_info=True)
        if transport is not None:
            try:
                transport.close()
            except Exception:
                log.debug("transfer transport.close failed", exc_info=True)


class TransferManager(QObject):
    """Process-global. Explorer sessions are not used for get/put."""

    job_updated = Signal(object)

    def __init__(
        self,
        profiles: ProfileManager,
        credentials: CredentialStore,
        known_hosts: KnownHostsStore,
        host_key_gate: HostKeyGate,
        settings: SettingsStore,
        *,
        on_host_key: Callable[[object], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._profiles = profiles
        self._credentials = credentials
        self._known_hosts = known_hosts
        self._gate = host_key_gate
        self._settings = settings
        self._on_host_key = on_host_key
        self._jobs: dict[str, TransferJob] = {}
        self._order: list[str] = []
        self._live: dict[str, tuple[NativeTransferThread, TransferWorkerSignals]] = {}
        self._watches: dict[str, _EditWatch] = {}

    def jobs(self) -> list[TransferJob]:
        return [self._jobs[i] for i in self._order if i in self._jobs]

    def job(self, job_id: str) -> TransferJob | None:
        return self._jobs.get(job_id)

    def has_active(self) -> bool:
        return any(
            j.status in (TransferStatus.QUEUED, TransferStatus.RUNNING, TransferStatus.PAUSED)
            for j in self._jobs.values()
        )

    def enqueue(self, job: TransferJob) -> TransferJob:
        if not job.id:
            job.id = str(uuid.uuid4())
        if not job.display_name:
            job.display_name = Path(job.local_path).name or posixpath.basename(job.remote_path)
        job.status = TransferStatus.QUEUED
        self._jobs[job.id] = job
        self._order.append(job.id)
        self.job_updated.emit(job)
        self._pump()
        return job

    def cancel(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.cancel_event.set()
        job.pause_event.clear()
        if job.status in (TransferStatus.QUEUED, TransferStatus.PAUSED):
            job.status = TransferStatus.CANCELLED
            job.finished_at = time.time()
            self.job_updated.emit(job)
        self._pump()

    def pause(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        if job.status is TransferStatus.QUEUED:
            job.status = TransferStatus.PAUSED
            self.job_updated.emit(job)
            return
        if job.status is TransferStatus.RUNNING:
            job.pause_event.set()
            job.status = TransferStatus.PAUSED
            self.job_updated.emit(job)

    def resume(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.status is not TransferStatus.PAUSED:
            return
        job.pause_event.clear()
        if job.id in self._live:
            job.status = TransferStatus.RUNNING
            self.job_updated.emit(job)
            return
        job.status = TransferStatus.QUEUED
        self.job_updated.emit(job)
        self._pump()

    def _pump(self) -> None:
        concurrency = max(1, int(self._settings.get().sftp.transfer_concurrency or 3))
        while len(self._live) < concurrency:
            nxt = next(
                (j for j in self.jobs() if j.status is TransferStatus.QUEUED),
                None,
            )
            if nxt is None:
                return
            self._start(nxt)

    def _start(self, job: TransferJob) -> None:
        try:
            profile = self._profiles.get(job.profile_id)
        except Exception as exc:
            job.status = TransferStatus.FAILED
            job.error = str(exc)
            job.finished_at = time.time()
            self.job_updated.emit(job)
            return
        job.status = TransferStatus.RUNNING
        job.started_at = time.time()
        job._speed_samples = []  # type: ignore[attr-defined]
        self.job_updated.emit(job)
        signals = TransferWorkerSignals(self)
        signals.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        signals.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        signals.host_key_prompt.connect(self._on_host_key, Qt.ConnectionType.QueuedConnection)
        timeout = float(profile.timeout_sec or self._settings.get().connections.timeout_sec)
        worker = TransferWorker(
            job,
            profile,
            self._credentials,
            self._known_hosts,
            self._gate,
            signals,
            timeout_sec=timeout,
        )
        thread = NativeTransferThread(worker, parent=self)
        self._live[job.id] = (thread, signals)
        thread.finished.connect(lambda jid=job.id: self._thread_done(jid))
        thread.start()

    def _on_progress(self, job_id: str, done: int, total: object) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.status not in (TransferStatus.RUNNING, TransferStatus.PAUSED):
            return
        job.nbytes_done = max(0, int(done))
        if isinstance(total, int) and total >= 0:
            job.nbytes_total = total
        now = time.monotonic()
        samples = getattr(job, "_speed_samples", None)
        if not isinstance(samples, list):
            samples = []
            job._speed_samples = samples  # type: ignore[attr-defined]
        averaged = note_speed_sample(samples, now, job.nbytes_done)
        if averaged is not None:
            job.speed_bps = averaged
        elif job.started_at and job.speed_bps <= 0 and job.nbytes_done > 0:
            elapsed = max(0.2, time.time() - job.started_at)
            job.speed_bps = job.nbytes_done / elapsed
        self.job_updated.emit(job)

    def _on_finished(self, job_id: str, ok: bool, message: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.finished_at = time.time()
        if job.cancel_event.is_set() and not ok:
            job.status = TransferStatus.CANCELLED
            job.error = None
        elif ok:
            job.status = TransferStatus.COMPLETED
            job.error = None
            if job.nbytes_total:
                job.nbytes_done = job.nbytes_total
        else:
            job.status = TransferStatus.FAILED
            job.error = message
        self.job_updated.emit(job)

    def _thread_done(self, job_id: str) -> None:
        live = self._live.pop(job_id, None)
        if live is not None:
            thread, _signals = live
            thread.deleteLater()
        self._pump()

    def watch_for_save(self, profile_id: str, local_path: str, remote_path: str) -> None:
        path = str(Path(local_path))
        existing = self._watches.get(path)
        if existing is not None:
            existing.stop()
        watch = _EditWatch(self, profile_id, path, remote_path)
        self._watches[path] = watch
        watch.start()

    def _enqueue_edit_upload(self, profile_id: str, local_path: str, remote_path: str) -> None:
        for job in self.jobs():
            if (
                job.profile_id == profile_id
                and job.direction is TransferDirection.UPLOAD
                and job.local_path == local_path
                and job.status in (TransferStatus.QUEUED, TransferStatus.RUNNING)
            ):
                return
        job = TransferJob(
            id=str(uuid.uuid4()),
            profile_id=profile_id,
            direction=TransferDirection.UPLOAD,
            local_path=local_path,
            remote_path=remote_path,
            is_dir=False,
            display_name=Path(local_path).name,
        )
        try:
            job.nbytes_total = int(Path(local_path).stat().st_size)
        except OSError:
            job.nbytes_total = None
        self.enqueue(job)


class _EditWatch:
    def __init__(
        self, manager: TransferManager, profile_id: str, local_path: str, remote_path: str
    ) -> None:
        from PySide6.QtCore import QFileSystemWatcher

        self._manager = manager
        self.profile_id = profile_id
        self.local_path = local_path
        self.remote_path = remote_path
        self._mtime = 0.0
        self._size = -1
        self._mtime, self._size = self._stat()
        self._watcher = QFileSystemWatcher(manager)
        self._debounce = QTimer(manager)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(700)
        self._poll = QTimer(manager)
        self._poll.setInterval(1000)
        self._debounce.timeout.connect(self._maybe_upload)
        self._poll.timeout.connect(self._poll_change)
        self._watcher.fileChanged.connect(self._on_changed)

    def start(self) -> None:
        if Path(self.local_path).is_file():
            self._watcher.addPath(self.local_path)
        self._poll.start()

    def stop(self) -> None:
        self._poll.stop()
        self._debounce.stop()
        files = self._watcher.files()
        if files:
            self._watcher.removePaths(files)

    def _stat(self) -> tuple[float, int]:
        try:
            st = Path(self.local_path).stat()
            return float(st.st_mtime), int(st.st_size)
        except OSError:
            return 0.0, -1

    def _on_changed(self, path: str) -> None:
        if path not in self._watcher.files() and Path(path).is_file():
            self._watcher.addPath(path)
        self._debounce.start()

    def _poll_change(self) -> None:
        mtime, size = self._stat()
        if mtime != self._mtime or size != self._size:
            self._debounce.start()

    def _maybe_upload(self) -> None:
        mtime, size = self._stat()
        if mtime == self._mtime and size == self._size:
            return
        if size < 0:
            return
        self._mtime, self._size = mtime, size
        self._manager._enqueue_edit_upload(self.profile_id, self.local_path, self.remote_path)
