from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ghostport import __version__
from ghostport.app import GhostportApp
from ghostport.constants import APP_NAME, APP_YEAR
from ghostport.install.location import default_install_dir, install_error
from ghostport.install.payload import copy_payload, launch_target
from ghostport.install.shortcuts import create_shortcut, desktop_dir, start_menu_dir
from ghostport.ui.theme import apply_theme, load_app_icon


class _InstallWorker(QThread):
    progress = Signal(int, int, str)
    failed = Signal(str)
    finished_ok = Signal(str)

    def __init__(
        self,
        dest: Path,
        *,
        desktop: bool,
        start_menu: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._dest = dest
        self._desktop = desktop
        self._start_menu = start_menu

    def run(self) -> None:
        try:
            copy_payload(self._dest, progress=self._on_copy)
            target, args, workdir, icon = launch_target(self._dest)
            if self._desktop:
                create_shortcut(
                    desktop_dir() / f"{APP_NAME}.lnk",
                    target=target,
                    arguments=args,
                    working_dir=workdir,
                    icon=icon,
                )
            if self._start_menu:
                create_shortcut(
                    start_menu_dir() / f"{APP_NAME}.lnk",
                    target=target,
                    arguments=args,
                    working_dir=workdir,
                    icon=icon,
                )
            self.finished_ok.emit(str(self._dest))
        except Exception as exc:
            self.failed.emit(str(exc))

    def _on_copy(self, done: int, total: int, name: str) -> None:
        self.progress.emit(done, total, name)


class InstallWizard(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} Setup")
        self.setMinimumSize(560, 420)
        self._dest = default_install_dir()
        self._worker: _InstallWorker | None = None
        self._installed: Path | None = None

        icon = load_app_icon()
        if icon is not None:
            self.setWindowIcon(icon)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._welcome_page())
        self.stack.addWidget(self._location_page())
        self.stack.addWidget(self._options_page())
        self.stack.addWidget(self._progress_page())
        self.stack.addWidget(self._done_page())

        self.back_btn = QPushButton("Back")
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("primaryButton")
        self.cancel_btn = QPushButton("Cancel")
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)
        self.cancel_btn.clicked.connect(self.reject)

        nav = QHBoxLayout()
        nav.addWidget(self.cancel_btn)
        nav.addStretch(1)
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.addWidget(self.stack, 1)
        root.addLayout(nav)
        self._sync_nav()

    def _welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        title = QLabel(f"Install {APP_NAME}")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        body = QLabel(
            f"{APP_NAME} is a portable SSH + SFTP client.\n\n"
            "This setup never asks for an administrator password.\n"
            "Nothing is written to Program Files or C:\\Windows.\n\n"
            "Files go in your user folder (or any folder you can write to).\n"
            f"Version {__version__}  ·  {APP_YEAR}"
        )
        body.setWordWrap(True)
        body.setStyleSheet("color: #9d9d9d; font-size: 14px;")
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addStretch(1)
        return page

    def _location_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        title = QLabel("Where should Ghostport live?")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        hint = QLabel(
            "Default is your user AppData folder — no admin rights.\n"
            "You can Browse to another drive if you do not want files on C:."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9d9d9d;")
        row = QHBoxLayout()
        self.path_edit = QLineEdit(str(self._dest))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        self.path_status = QLabel("")
        self.path_status.setWordWrap(True)
        self.path_edit.textChanged.connect(self._on_path_edited)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(row)
        layout.addWidget(self.path_status)
        layout.addStretch(1)
        self._validate_path()
        return page

    def _options_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Shortcuts")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        hint = QLabel("These go on your desktop and Start menu only — not for all users.")
        hint.setStyleSheet("color: #9d9d9d;")
        self.desktop_box = QCheckBox("Desktop shortcut")
        self.desktop_box.setChecked(True)
        self.start_box = QCheckBox("Start menu shortcut")
        self.start_box.setChecked(True)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addSpacing(8)
        layout.addWidget(self.desktop_box)
        layout.addWidget(self.start_box)
        layout.addStretch(1)
        return page

    def _progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Installing…")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.progress_label = QLabel("Preparing")
        self.progress_label.setStyleSheet("color: #9d9d9d;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        layout.addWidget(title)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.bar)
        layout.addStretch(1)
        return page

    def _done_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.done_title = QLabel("Ghostport is ready")
        self.done_title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.done_body = QLabel("")
        self.done_body.setWordWrap(True)
        self.done_body.setStyleSheet("color: #9d9d9d; font-size: 14px;")
        self.launch_box = QCheckBox("Open Ghostport now")
        self.launch_box.setChecked(True)
        layout.addWidget(self.done_title)
        layout.addWidget(self.done_body)
        layout.addSpacing(8)
        layout.addWidget(self.launch_box)
        layout.addStretch(1)
        return page

    def _on_path_edited(self) -> None:
        ok = self._validate_path()
        if self.stack.currentIndex() == 1:
            self.next_btn.setEnabled(ok)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose a folder Ghostport can write to",
            str(self._dest),
        )
        if chosen:
            dest = Path(chosen)
            if dest.name.lower() != "ghostport":
                dest = dest / "Ghostport"
            self.path_edit.setText(str(dest))

    def _validate_path(self) -> bool:
        raw = self.path_edit.text().strip()
        path = Path(raw) if raw else Path()
        err = install_error(path) if raw else "Choose a folder."
        if err:
            self.path_status.setText(err)
            self.path_status.setStyleSheet("color: #c45454;")
            return False
        self._dest = path
        self.path_status.setText("This folder does not need administrator permission.")
        self.path_status.setStyleSheet("color: #3d9b94;")
        return True

    def _sync_nav(self) -> None:
        page = self.stack.currentIndex()
        self.back_btn.setEnabled(page > 0 and page < 3)
        self.cancel_btn.setEnabled(page != 3)
        if page == 0:
            self.next_btn.setText("Next")
            self.next_btn.setEnabled(True)
        elif page == 1:
            self.next_btn.setText("Next")
            self.next_btn.setEnabled(self._validate_path())
        elif page == 2:
            self.next_btn.setText("Install")
            self.next_btn.setEnabled(True)
        elif page == 3:
            self.next_btn.setEnabled(False)
            self.back_btn.setEnabled(False)
        else:
            self.next_btn.setText("Finish")
            self.next_btn.setEnabled(True)
            self.back_btn.setEnabled(False)
            self.cancel_btn.setEnabled(False)

    def _back(self) -> None:
        page = self.stack.currentIndex()
        if page > 0 and page < 3:
            self.stack.setCurrentIndex(page - 1)
        self._sync_nav()

    def _next(self) -> None:
        page = self.stack.currentIndex()
        if page == 1 and not self._validate_path():
            return
        if page == 2:
            self._start_install()
            return
        if page == 4:
            self._finish()
            return
        self.stack.setCurrentIndex(page + 1)
        self._sync_nav()

    def _start_install(self) -> None:
        if not self._validate_path():
            self.stack.setCurrentIndex(1)
            self._sync_nav()
            return
        self.stack.setCurrentIndex(3)
        self._sync_nav()
        self.bar.setValue(0)
        worker = _InstallWorker(
            self._dest,
            desktop=self.desktop_box.isChecked(),
            start_menu=self.start_box.isChecked(),
            parent=self,
        )
        worker.progress.connect(self._on_progress)
        worker.failed.connect(self._on_fail)
        worker.finished_ok.connect(self._on_ok)
        self._worker = worker
        worker.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)
        self.progress_label.setText(name)

    def _on_fail(self, message: str) -> None:
        QMessageBox.warning(self, "Install failed", message)
        self.stack.setCurrentIndex(2)
        self._sync_nav()

    def _on_ok(self, dest: str) -> None:
        self._installed = Path(dest)
        self.done_body.setText(
            f"Installed to:\n{dest}\n\n"
            "No administrator password was used.\n"
            "Open Ghostport from the shortcut, or click Finish."
        )
        self.stack.setCurrentIndex(4)
        self._sync_nav()

    def _finish(self) -> None:
        if self.launch_box.isChecked() and self._installed is not None:
            target, args, workdir, _icon = launch_target(self._installed)
            from PySide6.QtCore import QProcess

            QProcess.startDetached(target, args.split() if args else [], workdir)
        self.accept()


def run_wizard(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = GhostportApp(argv)
    from ghostport.config.settings import Settings

    apply_theme(app, Settings())
    icon = load_app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    wizard = InstallWizard()
    wizard.show()
    return app.exec()
