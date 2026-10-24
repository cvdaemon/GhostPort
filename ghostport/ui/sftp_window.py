from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ghostport.app import GhostportApp
from ghostport.constants import APP_NAME, MAX_SPLIT_PANES
from ghostport.sftp.transfer_manager import TransferJob, TransferManager, TransferStatus
from ghostport.ui.sftp_tab import SftpTab
from ghostport.ui.split_workspace import SessionPane, SplitWorkspace
from ghostport.ui.widgets.transfer_queue import TransferQueueView
from shiboken6 import isValid


class SftpWindow(QMainWindow):
    """Does not bind host_key_decision_needed — MainWindow owns that."""

    def __init__(
        self,
        app: GhostportApp,
        *,
        primary: bool,
        on_closed=None,
        on_open_ssh: Callable[[str], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self.primary = primary
        self._on_closed = on_closed
        self._on_open_ssh = on_open_ssh
        self.setWindowTitle(f"{APP_NAME} SFTP")
        self.resize(1100, 680)
        self.workspace = SplitWorkspace(self, home_locked=False)
        self.tabs = self.workspace.tabs

        self.ssh_btn = QPushButton("SSH")
        self.ssh_btn.setObjectName("primaryButton")
        self.ssh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ssh_btn.setToolTip("Open an SSH session for this SFTP tab")
        self.ssh_btn.clicked.connect(self._open_ssh_current)
        plus = QPushButton("+")
        plus.setFixedWidth(32)
        plus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        plus.clicked.connect(self._pick_profile)
        hint = QLabel(f"Drag a tab to an edge to split  ·  drag the divider to resize  ·  max {MAX_SPLIT_PANES}")
        hint.setStyleSheet("color: #6b6b6b; font-size: 11px;")
        chrome = QWidget()
        chrome.setFixedHeight(40)
        row = QHBoxLayout(chrome)
        row.setContentsMargins(10, 4, 10, 4)
        row.addWidget(self.ssh_btn)
        row.addWidget(hint, 1)
        row.addWidget(plus)

        shell = QWidget()
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(chrome)
        layout.addWidget(self.workspace, 1)
        self.setCentralWidget(shell)

        self.workspace.tab_close_requested.connect(self._close_widget)
        self.workspace.current_widget_changed.connect(self._on_current)
        self.workspace.tab_menu_requested.connect(self._tab_menu)
        self._transfer_view: TransferQueueView | None = None
        self._creating_transfers = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(1800)
        self._hide_timer.timeout.connect(self._hide_transfers_if_idle)
        transfers = getattr(self._app, "transfers", None)
        if isinstance(transfers, TransferManager):
            transfers.job_updated.connect(self._on_transfer_job)
        self._sync_ssh_button()

    def add_tab(
        self,
        profile_id: str,
        *,
        force_new: bool = False,
        remote_cwd: str | None = None,
    ) -> SftpTab | None:
        cwd = (remote_cwd or "").strip() or None
        if not force_new:
            existing = self._find_profile_tab(profile_id)
            if existing is not None:
                pane, index = existing
                pane.tabs.setCurrentIndex(index)
                self.workspace.set_active(pane)
                self._sync_title()
                self.show()
                self.raise_()
                self.activateWindow()
                tab = pane.tabs.widget(index)
                assert isinstance(tab, SftpTab)
                if cwd:
                    tab.navigate_remote(cwd)
                return tab
        profile = self._app.profiles.get(profile_id)
        pane = self.workspace.live_pane()
        if pane is None or not isValid(pane.tabs):
            QMessageBox.warning(self, "SFTP", "The session area is not available.")
            return None
        session = self._app.connections.start_sftp(
            profile_id, parent=self, initial_directory=cwd
        )
        tab = SftpTab(
            profile,
            session,
            self._app.settings,
            parent=pane.tabs,
            transfers=getattr(self._app, "transfers", None),
            temp_dir=self._app.paths.temp_dir,
        )
        pane.tabs.addTab(tab, tab.display_title())
        pane.tabs.setCurrentWidget(tab)
        tab.close_requested.connect(lambda t=tab: self._close_widget(t))
        tab.reconnect_requested.connect(session.reconnect)
        session.thread_finished.connect(lambda t=tab: self._sftp_thread_done(t))
        self._sync_title()
        self._sync_ssh_button()
        self.show()
        self.raise_()
        self.activateWindow()
        return tab

    def focus_profile(self, profile_id: str) -> bool:
        found = self._find_profile_tab(profile_id)
        if found is None:
            return False
        pane, index = found
        pane.tabs.setCurrentIndex(index)
        self.show()
        self.raise_()
        self.activateWindow()
        return True

    def _find_profile_tab(self, profile_id: str) -> tuple[SessionPane, int] | None:
        for pane in self.workspace.panes():
            for i in range(pane.tabs.count()):
                widget = pane.tabs.widget(i)
                if isinstance(widget, SftpTab) and widget.profile_id == profile_id:
                    return pane, i
        return None

    def _locate(self, tab: QWidget) -> tuple[SessionPane, int] | None:
        return self.workspace.locate(tab)

    def _close_widget(self, widget: QWidget) -> None:
        if isinstance(widget, TransferQueueView):
            self._remove_transfers_tab()
            return
        if not isinstance(widget, SftpTab):
            return
        found = self._locate(widget)
        if found is not None:
            pane, index = found
            pane.tabs.removeTab(index)
            self.workspace._collapse_empty(pane)
        widget.hide()
        widget.setParent(self)
        widget.shutdown()
        if not any(isinstance(w, SftpTab) for w in self.workspace.iter_widgets()):
            self.close()

    def _on_transfer_job(self, job: object) -> None:
        if not isinstance(job, TransferJob):
            return
        self._ensure_transfers_tab()
        transfers = getattr(self._app, "transfers", None)
        if not isinstance(transfers, TransferManager):
            return
        if transfers.has_active() or job.status is TransferStatus.FAILED:
            self._hide_timer.stop()
            return
        self._hide_timer.start()

    def _find_transfers_view(self) -> TransferQueueView | None:
        if isinstance(self._transfer_view, TransferQueueView) and self._locate(self._transfer_view) is not None:
            return self._transfer_view
        for widget in self.workspace.iter_widgets():
            if isinstance(widget, TransferQueueView):
                return widget
        return None

    def _ensure_transfers_tab(self) -> None:
        existing = self._find_transfers_view()
        if existing is not None:
            self._transfer_view = existing
            found = self._locate(existing)
            if found is not None:
                pane, index = found
                pane.tabs.setTabText(index, "Transfers")
            return
        if self._creating_transfers:
            return
        transfers = getattr(self._app, "transfers", None)
        if not isinstance(transfers, TransferManager):
            return
        pane = self.workspace.live_pane()
        if pane is None or not isValid(pane.tabs):
            return
        self._creating_transfers = True
        try:
            view = TransferQueueView(transfers, parent=pane.tabs)
            self._transfer_view = view
            pane.tabs.addTab(view, "Transfers")
            pane.tabs.setCurrentWidget(view)
        finally:
            self._creating_transfers = False

    def _hide_transfers_if_idle(self) -> None:
        transfers = getattr(self._app, "transfers", None)
        if isinstance(transfers, TransferManager) and transfers.has_active():
            return
        self._remove_transfers_tab()

    def _remove_transfers_tab(self) -> None:
        view = self._transfer_view
        if view is None:
            return
        found = self._locate(view)
        if found is not None:
            pane, index = found
            pane.tabs.removeTab(index)
            self.workspace._collapse_empty(pane)
        view.deleteLater()
        self._transfer_view = None

    def _sftp_thread_done(self, tab: SftpTab) -> None:
        if tab.session._restart:
            return
        if self._locate(tab) is not None:
            return
        tab.deleteLater()

    def _on_current(self, widget: object) -> None:
        self._sync_title()
        self._sync_ssh_button()

    def _sync_title(self) -> None:
        pane = self.workspace.live_pane()
        widget = pane.tabs.currentWidget() if pane is not None else None
        if isinstance(widget, SftpTab):
            self.setWindowTitle(f"{APP_NAME} SFTP — {widget.display_title()}")
        else:
            self.setWindowTitle(f"{APP_NAME} SFTP")

    def _sync_ssh_button(self) -> None:
        tab = self._current_sftp_tab()
        self.ssh_btn.setEnabled(tab is not None)
        if tab is None:
            self.ssh_btn.setToolTip("Select an SFTP tab, then open SSH")
        else:
            self.ssh_btn.setToolTip(f"Open SSH for {tab.profile.name}")

    def _current_sftp_tab(self) -> SftpTab | None:
        pane = self.workspace.live_pane()
        if pane is None:
            return None
        current = pane.tabs.currentWidget()
        if isinstance(current, SftpTab):
            return current
        for widget in self.workspace.iter_widgets():
            if isinstance(widget, SftpTab):
                return widget
        return None

    def _open_ssh_current(self) -> None:
        tab = self._current_sftp_tab()
        if tab is None or self._on_open_ssh is None:
            return
        self._on_open_ssh(tab.profile_id)
        from ghostport.ui.main_window import MainWindow

        for widget in self._app.topLevelWidgets():
            if isinstance(widget, MainWindow):
                widget.show()
                widget.raise_()
                widget.activateWindow()
                break

    def _tab_menu(self, pane: SessionPane, pos) -> None:
        index = pane.tabs.tabBar().tabAt(pos)
        if index < 0:
            return
        tab = pane.tabs.widget(index)
        if not isinstance(tab, SftpTab):
            return
        menu = QMenu(self)
        dup = menu.addAction("Duplicate Tab")
        rec = menu.addAction("Reconnect")
        disc = menu.addAction("Disconnect")
        rename = menu.addAction("Rename")
        menu.addSeparator()
        close = menu.addAction("Close")
        chosen = menu.exec(pane.tabs.tabBar().mapToGlobal(pos))
        if chosen is dup:
            self.add_tab(tab.profile_id, force_new=True)
        elif chosen is rec:
            tab.session.reconnect()
        elif chosen is disc:
            tab.session.disconnect()
        elif chosen is rename:
            text, ok = QInputDialog.getText(self, "Rename tab", "Name:", text=tab.display_title())
            if ok and text.strip():
                tab.title_override = text.strip()
                found = self._locate(tab)
                if found:
                    p, i = found
                    p.tabs.setTabText(i, tab.display_title())
                self._sync_title()
        elif chosen is close:
            self._close_widget(tab)

    def _pick_profile(self) -> None:
        names = [p.name for p in self._app.profiles.all()]
        if not names:
            return
        name, ok = QInputDialog.getItem(self, "Open SFTP", "Server:", names, 0, False)
        if not ok:
            return
        for profile in self._app.profiles.all():
            if profile.name == name:
                self.add_tab(profile.id, force_new=True)
                return

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        transfers = getattr(self._app, "transfers", None)
        if isinstance(transfers, TransferManager) and transfers.has_active():
            box = QMessageBox.question(
                self,
                "Transfers running",
                "Files are still copying. Close SFTP and cancel them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if box != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for job in transfers.jobs():
                if job.status in (TransferStatus.QUEUED, TransferStatus.RUNNING):
                    transfers.cancel(job.id)
        for widget in list(self.workspace.iter_widgets()):
            if isinstance(widget, SftpTab):
                widget.shutdown()
        if isinstance(transfers, TransferManager):
            try:
                transfers.job_updated.disconnect(self._on_transfer_job)
            except RuntimeError:
                pass
        if callable(self._on_closed):
            self._on_closed(self)
        super().closeEvent(event)
