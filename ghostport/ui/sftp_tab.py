from __future__ import annotations

import posixpath
import subprocess
import uuid
from pathlib import Path

from PySide6.QtCore import QEvent, QProcess, QStandardPaths, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ghostport.config.settings import Settings, SettingsStore
from ghostport.errors import GhostportError
from ghostport.profiles.models import ServerProfile
from ghostport.sftp.file_model import FileEntry
from ghostport.sftp.session import SftpSession
from ghostport.sftp.transfer_manager import TransferDirection, TransferJob, TransferManager, TransferStatus
from ghostport.ssh.session import ConnectionState
from ghostport.ui.widgets.file_pane import FilePane
from ghostport.ui.widgets.idle_closer import IdleCloser


def default_local_directory(settings: Settings) -> str:
    raw = (settings.sftp.default_local_directory or "").strip()
    if raw:
        path = Path(raw)
        if path.is_dir():
            return str(path)
    home = Path.home()
    downloads = home / "Downloads"
    if downloads.is_dir():
        return str(downloads)
    return str(home)


class SftpTab(QWidget):
    close_requested = Signal()
    reconnect_requested = Signal()

    def __init__(
        self,
        profile: ServerProfile,
        session: SftpSession,
        settings: Settings | SettingsStore,
        parent: QWidget | None = None,
        *,
        transfers: TransferManager | None = None,
        temp_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.profile_id = profile.id
        self.profile = profile
        self.session = session
        self._transfers = transfers
        self._temp_root = temp_dir
        if isinstance(settings, SettingsStore):
            self._store = settings
            self._settings = settings.get()
        else:
            self._store = None
            self._settings = settings
        self.title_override: str | None = None
        self._last_error: GhostportError | None = None
        self._pending: dict[str, tuple[str, str, str]] = {}
        self._clip: list[FileEntry] = []
        self._clip_cut = False

        self.local = FilePane("LOCAL", remote=False, settings_store=self._store)
        self.remote = FilePane(f"REMOTE — {profile.name}", remote=True, settings_store=self._store)
        self.local.set_show_hidden(self._settings.sftp.show_hidden_files)
        self.remote.set_show_hidden(self._settings.sftp.show_hidden_files)
        self.local.navigate(default_local_directory(self._settings))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.local)
        splitter.addWidget(self.remote)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        self._overlay = QWidget(self)
        self._overlay.setStyleSheet("background-color: rgba(18, 18, 18, 235);")
        self._title = QLabel("Connecting...")
        self._title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self._body = QLabel(f"{profile.host}:{profile.port}")
        self._body.setWordWrap(True)
        self._reconnect = QPushButton("Reconnect")
        self._reconnect.setObjectName("primaryButton")
        self._close = QPushButton("Close Session")
        for btn in (self._reconnect, self._close):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._reconnect)
        row.addWidget(self._close)
        row.addStretch(1)
        ov = QVBoxLayout(self._overlay)
        ov.addStretch(1)
        ov.addWidget(self._title, alignment=Qt.AlignmentFlag.AlignHCenter)
        ov.addWidget(self._body, alignment=Qt.AlignmentFlag.AlignHCenter)
        ov.addSpacing(12)
        ov.addLayout(row)
        ov.addStretch(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(splitter, 1)
        self._overlay.setParent(self)

        session.listing_ready.connect(self._on_listing)
        session.cwd_changed.connect(self._on_cwd)
        session.state_changed.connect(self._on_state)
        session.error_occurred.connect(self._on_error)
        session.op_finished.connect(self._on_op)
        self.remote.path_changed.connect(session.listdir)
        self.remote.activated_dir.connect(session.listdir)
        self.remote.activated_file.connect(self._open_remote)
        self.remote.context_requested.connect(self._remote_menu)
        self.remote.local_paths_dropped.connect(self._upload_local_paths)
        self.local.remote_entries_dropped.connect(self._download_remote_entries)
        self.local.mkdir_requested.connect(self._mkdir_local)
        self.remote.mkdir_requested.connect(self._mkdir_remote)
        self.local.mkfile_requested.connect(self._mkfile_local)
        self.remote.mkfile_requested.connect(self._mkfile_remote)
        if self._transfers is not None:
            self._transfers.job_updated.connect(self._on_job)
        self._reconnect.clicked.connect(self.reconnect_requested)
        self._close.clicked.connect(self.close_requested)
        self._idle = IdleCloser(self._settings.connections.idle_timeout_min, self)
        self._idle.idle.connect(self._on_idle)
        self.installEventFilter(self)
        self.local.installEventFilter(self)
        self.remote.installEventFilter(self)
        self.local.view.installEventFilter(self)
        self.remote.view.installEventFilter(self)
        self.local.path_bar.installEventFilter(self)
        self.remote.path_bar.installEventFilter(self)
        self._on_state(session.state)

    def display_title(self) -> str:
        return self.title_override or self.profile.name

    def navigate_remote(self, path: str) -> None:
        target = (path or "").strip()
        if target:
            self.session.listdir(target)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._overlay.setGeometry(self.rect())

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())

    def _on_listing(self, path: str, entries: object) -> None:
        rows = list(entries) if isinstance(entries, list) else []
        typed = [e for e in rows if isinstance(e, FileEntry)]
        self.remote.set_remote_entries(path, typed)

    def _on_cwd(self, path: str) -> None:
        self.remote.path_bar.setText(path)

    def _on_error(self, error: GhostportError) -> None:
        self._last_error = error
        self._body.setText(error.user_body())

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        etype = event.type()
        if etype in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel):
            self._idle.touch()
        return super().eventFilter(watched, event)

    def _profile_busy(self) -> bool:
        if self._transfers is None:
            return False
        active = (TransferStatus.QUEUED, TransferStatus.RUNNING, TransferStatus.PAUSED)
        return any(j.profile_id == self.profile_id and j.status in active for j in self._transfers.jobs())

    def _on_idle(self) -> None:
        if self._profile_busy():
            self._idle.touch()
            return
        self.close_requested.emit()

    def _on_state(self, state: ConnectionState) -> None:
        self._idle.set_armed(state is ConnectionState.CONNECTED)
        if state is ConnectionState.CONNECTED:
            self._overlay.hide()
            return
        self._overlay.show()
        self._overlay.raise_()
        self._overlay.setGeometry(self.rect())
        if state in (ConnectionState.CONNECTING, ConnectionState.RECONNECTING):
            self._title.setText("Connecting...")
            self._body.setText(f"{self.profile.host}:{self.profile.port}")
            self._reconnect.hide()
        elif state is ConnectionState.FAILED:
            self._title.setText("SFTP failed")
            self._body.setText(self._last_error.user_body() if self._last_error else "Unable to connect.")
            self._reconnect.show()
        else:
            self._title.setText("Disconnected")
            self._body.setText(f"{self.profile.name}\n{self.profile.host}:{self.profile.port}")
            self._reconnect.show()

    def shutdown(self) -> None:
        self._idle.set_armed(False)
        if self._transfers is not None:
            try:
                self._transfers.job_updated.disconnect(self._on_job)
            except RuntimeError:
                pass
        self.session.disconnect()

    def _temp_dir(self) -> Path:
        if self._temp_root is not None:
            path = Path(self._temp_root) / "edit"
        else:
            root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))
            path = root / "Ghostport" / "edit"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _pick_editor(self, *, force: bool) -> str | None:
        stored = (self._settings.sftp.editor_path or "").strip()
        if not force and stored and Path(stored).is_file():
            return stored
        start = str(Path(stored).parent) if stored and Path(stored).parent.is_dir() else r"C:\Program Files"
        program, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose file editor",
            start,
            "Programs (*.exe);;All files (*.*)",
        )
        if not program:
            return None
        self._settings.sftp.editor_path = program
        if self._store is not None:
            self._store.save(self._settings)
        return program

    def _remote_menu(self, entries: object, global_pos) -> None:
        items = list(entries) if isinstance(entries, list) else []
        items = [e for e in items if isinstance(e, FileEntry)]
        menu = QMenu(self)
        download = menu.addAction("Download")
        open_act = menu.addAction("Open")
        edit_act = menu.addAction("Edit")
        edit_with = menu.addAction("Edit with…")
        move_act = menu.addAction("Move to…")
        menu.addSeparator()
        new_folder = menu.addAction("New folder")
        new_file = menu.addAction("New file")
        delete_act = menu.addAction("Erase/delete")
        rename_act = menu.addAction("Rename")
        open_with = menu.addAction("Open with…")
        menu.addSeparator()
        cut_act = menu.addAction("Cut")
        copy_act = menu.addAction("Copy")
        paste_act = menu.addAction("Paste")
        copy_path = menu.addAction("Copy full path")
        menu.addSeparator()
        props = menu.addAction("Properties")

        has = bool(items)
        files_only = has and all(not e.is_dir for e in items)
        one = len(items) == 1
        for act in (download, open_act, move_act, delete_act, cut_act, copy_act, copy_path, props):
            act.setEnabled(has)
        edit_act.setEnabled(files_only)
        edit_with.setEnabled(files_only and one)
        open_with.setEnabled(files_only and one)
        rename_act.setEnabled(one)
        paste_act.setEnabled(bool(self._clip))

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is download:
            self._download(items)
        elif chosen is new_folder:
            self._mkdir_remote()
        elif chosen is new_file:
            self._mkfile_remote()
        elif chosen is open_act:
            if one and items[0].is_dir:
                self.session.listdir(items[0].path)
            else:
                self._download(items, after="open")
        elif chosen is edit_act:
            self._edit(items, force_picker=False)
        elif chosen is edit_with:
            self._edit(items, force_picker=True)
        elif chosen is move_act:
            self._move_to(items)
        elif chosen is delete_act:
            self._delete(items)
        elif chosen is rename_act:
            self._rename(items[0])
        elif chosen is open_with:
            self._download(items, after="openwith")
        elif chosen is cut_act:
            self._clip = list(items)
            self._clip_cut = True
        elif chosen is copy_act:
            self._clip = list(items)
            self._clip_cut = False
            QGuiApplication.clipboard().setText("\n".join(e.path for e in items))
        elif chosen is paste_act:
            self._paste()
        elif chosen is copy_path:
            QGuiApplication.clipboard().setText("\n".join(e.path for e in items))
        elif chosen is props:
            self._properties(items[0] if items else None)

    def _open_remote(self, path: str) -> None:
        entries = [e for e in self.remote.selected_entries() if e.path == path]
        if not entries:
            entries = [FileEntry(name=posixpath.basename(path), path=path, is_dir=False, size=0, modified_ts=None, mode=None)]
        self._download(entries, after="open")

    def _dest_local(self, name: str) -> Path:
        folder = Path(self.local.current_path() or default_local_directory(self._settings))
        return folder / name

    def _entry_name(self, title: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, "Name:")
        if not ok:
            return None
        name = name.strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            QMessageBox.warning(self, title, "Enter a name without slashes.")
            return None
        return name

    def _mkdir_local(self) -> None:
        name = self._entry_name("New folder")
        if not name:
            return
        parent = Path(self.local.current_path() or default_local_directory(self._settings))
        dest = parent / name
        if dest.exists():
            QMessageBox.warning(self, "New folder", f"{dest} already exists.")
            return
        try:
            dest.mkdir()
        except OSError as exc:
            QMessageBox.warning(self, "New folder", str(exc))
            return
        self.local.refresh()
        self.local.navigate(str(parent))

    def _mkfile_local(self) -> None:
        name = self._entry_name("New file")
        if not name:
            return
        parent = Path(self.local.current_path() or default_local_directory(self._settings))
        dest = parent / name
        if dest.exists():
            QMessageBox.warning(self, "New file", f"{dest} already exists.")
            return
        try:
            dest.write_bytes(b"")
        except OSError as exc:
            QMessageBox.warning(self, "New file", str(exc))
            return
        self.local.refresh()
        self.local.navigate(str(parent))

    def _remote_child(self, name: str) -> str:
        cwd = self.remote.current_path() or "/"
        return cwd.rstrip("/") + "/" + name if cwd != "/" else "/" + name

    def _mkdir_remote(self) -> None:
        name = self._entry_name("New folder")
        if not name:
            return
        req = str(uuid.uuid4())
        self._pending[req] = ("", "", "")
        self.session.mkdir(self._remote_child(name), req)

    def _mkfile_remote(self) -> None:
        name = self._entry_name("New file")
        if not name:
            return
        req = str(uuid.uuid4())
        self._pending[req] = ("", "", "")
        self.session.mkfile(self._remote_child(name), req)

    def _edit(self, items: list[FileEntry], *, force_picker: bool) -> None:
        files = [e for e in items if not e.is_dir]
        if not files:
            return
        editor = self._pick_editor(force=force_picker)
        if not editor:
            return
        dest_root = self._temp_dir() / self.profile_id[:8] / uuid.uuid4().hex[:8]
        dest_root.mkdir(parents=True, exist_ok=True)
        self._download(files, after="edit", dest_root=dest_root, editor=editor)

    def _download_remote_entries(self, entries: object) -> None:
        items = [e for e in (entries or []) if isinstance(e, FileEntry)]
        if items:
            self._download(items)

    def _upload_local_paths(self, paths: object) -> None:
        locals_ = [str(p) for p in (paths or []) if str(p)]
        remote_dir = self.remote.current_path() or "/"
        for local in locals_:
            src = Path(local)
            if not src.exists():
                continue
            name = src.name
            dest = remote_dir.rstrip("/") + "/" + name if remote_dir != "/" else "/" + name
            self._enqueue(
                TransferDirection.UPLOAD,
                local=str(src),
                remote=dest,
                is_dir=src.is_dir(),
                size=src.stat().st_size if src.is_file() else None,
            )

    def _download(
        self,
        items: list[FileEntry],
        *,
        after: str = "",
        dest_root: Path | None = None,
        editor: str = "",
    ) -> None:
        if dest_root is None:
            dest_root = self._temp_dir() if after else Path(
                self.local.current_path() or default_local_directory(self._settings)
            )
        dest_root.mkdir(parents=True, exist_ok=True)
        for entry in items:
            dest = dest_root / entry.name
            if dest.exists() and self._settings.sftp.confirm_overwrites and not after:
                box = QMessageBox.question(
                    self,
                    "Overwrite?",
                    f"{dest} already exists. Replace it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if box != QMessageBox.StandardButton.Yes:
                    continue
            self._enqueue(
                TransferDirection.DOWNLOAD,
                local=str(dest),
                remote=entry.path,
                is_dir=entry.is_dir,
                size=None if entry.is_dir else entry.size,
                after=after,
                editor=editor,
            )

    def _enqueue(
        self,
        direction: TransferDirection,
        *,
        local: str,
        remote: str,
        is_dir: bool,
        size: int | None,
        after: str = "",
        editor: str = "",
    ) -> None:
        self._idle.touch()
        if self._transfers is None:
            req = str(uuid.uuid4())
            self._pending[req] = (after, editor, remote)
            if direction is TransferDirection.DOWNLOAD:
                self.session.download(remote, local, is_dir=is_dir, request_id=req)
            else:
                QMessageBox.warning(self, "SFTP", "Uploads need an active transfer manager.")
            return
        job = TransferJob(
            id=str(uuid.uuid4()),
            profile_id=self.profile_id,
            direction=direction,
            local_path=local,
            remote_path=remote,
            is_dir=is_dir,
            nbytes_total=None if is_dir else size,
            display_name=Path(local).name or posixpath.basename(remote),
        )
        self._pending[job.id] = (after, editor, remote)
        self._transfers.enqueue(job)

    def _move_to(self, items: list[FileEntry]) -> None:
        dest, ok = QInputDialog.getText(
            self,
            "Move to…",
            "Remote folder:",
            text=self.remote.current_path() or "/",
        )
        if not ok or not dest.strip():
            return
        dest = dest.strip().rstrip("/") or "/"
        for entry in items:
            target = dest + "/" + entry.name if dest != "/" else "/" + entry.name
            req = str(uuid.uuid4())
            self._pending[req] = ("", "", "")
            self.session.rename(entry.path, target, req)

    def _delete(self, items: list[FileEntry]) -> None:
        names = ", ".join(e.name for e in items[:8])
        extra = "" if len(items) <= 8 else f" and {len(items) - 8} more"
        if self._settings.sftp.confirm_deletes:
            box = QMessageBox.question(
                self,
                "Erase/delete",
                f"Delete {names}{extra}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if box != QMessageBox.StandardButton.Yes:
                return
        for entry in items:
            req = str(uuid.uuid4())
            self._pending[req] = ("", "", "")
            self.session.remove(entry.path, recursive=entry.is_dir, request_id=req)

    def _rename(self, entry: FileEntry) -> None:
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=entry.name)
        if not ok or not name.strip() or name.strip() == entry.name:
            return
        parent = posixpath.dirname(entry.path.rstrip("/")) or "/"
        dest = parent.rstrip("/") + "/" + name.strip() if parent != "/" else "/" + name.strip()
        req = str(uuid.uuid4())
        self._pending[req] = ("", "", "")
        self.session.rename(entry.path, dest, req)

    def _paste(self) -> None:
        cwd = self.remote.current_path() or "/"
        for entry in self._clip:
            dest = cwd.rstrip("/") + "/" + entry.name if cwd != "/" else "/" + entry.name
            req = str(uuid.uuid4())
            self._pending[req] = ("", "", "")
            if self._clip_cut:
                self.session.rename(entry.path, dest, req)
            else:
                QMessageBox.information(
                    self,
                    "Copy",
                    "Remote copy without cut is not a server-side copy. Use Download, then upload from the local pane in a later build.\nCut + Paste moves on the server.",
                )
                return
        if self._clip_cut:
            self._clip = []
            self._clip_cut = False

    def _properties(self, entry: FileEntry | None) -> None:
        if entry is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Properties")
        form = QFormLayout(dlg)
        form.addRow("Name", QLabel(entry.name))
        form.addRow("Full path", QLabel(entry.path))
        form.addRow("Type", QLabel(entry.type_label()))
        form.addRow("Size", QLabel(entry.size_label() or "—"))
        form.addRow("Modified", QLabel(entry.modified_label() or "—"))
        form.addRow("Permissions", QLabel(entry.mode_label() or "—"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        form.addRow(buttons)
        dlg.exec()

    def _on_op(self, request_id: str, ok: bool, message: str, extra: object) -> None:
        pending = self._pending.pop(request_id, None)
        if not ok:
            QMessageBox.warning(self, "SFTP", message)
            return
        if pending is None:
            return
        after, editor, remote = pending
        local = str(extra) if extra else ""
        self._finish_after(after, local, editor, remote)

    def _on_job(self, job: object) -> None:
        if not isinstance(job, TransferJob) or job.profile_id != self.profile_id:
            return
        if job.status in (TransferStatus.QUEUED, TransferStatus.RUNNING, TransferStatus.PAUSED):
            self._idle.touch()
            return
        if job.status not in (TransferStatus.COMPLETED, TransferStatus.FAILED, TransferStatus.CANCELLED):
            return
        pending = self._pending.pop(job.id, None)
        if job.status is TransferStatus.FAILED:
            QMessageBox.warning(self, "SFTP", job.error or "Transfer failed")
            return
        if job.status is TransferStatus.CANCELLED:
            return
        after, editor, remote = pending if pending is not None else ("", "", "")
        self._finish_after(after, job.local_path, editor, remote or job.remote_path)
        if job.direction is TransferDirection.DOWNLOAD and not after:
            self.local.refresh()
        if job.direction is TransferDirection.UPLOAD:
            self.session.listdir(self.remote.current_path() or "/")

    def _finish_after(self, after: str, local: str, editor: str, remote: str) -> None:
        if after == "open" and local:
            QDesktopServices.openUrl(QUrl.fromLocalFile(local))
            return
        if after == "edit" and local:
            if editor:
                QProcess.startDetached(editor, [local])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(local))
            if self._transfers is not None and remote:
                self._transfers.watch_for_save(self.profile_id, local, remote)
            return
        if after == "openwith" and local:
            subprocess.Popen(
                ["rundll32.exe", "shell32.dll,OpenAs_RunDLL", local],
                close_fds=True,
            )

