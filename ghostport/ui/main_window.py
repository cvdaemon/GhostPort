from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from ghostport.app import GhostportApp
from ghostport.constants import APP_NAME, MAX_SPLIT_PANES
from ghostport.errors import GhostportError, HostKeyAborted
from ghostport.ssh.known_hosts import HostKeyDecision, HostKeyPrompt
from ghostport.ssh.session import ConnectionState
from ghostport.ui.connection_error_dialog import ConnectionErrorDialog
from ghostport.ui.home_page import HomePage
from ghostport.ui.host_key_dialog import HostKeyDialog
from ghostport.ui.session_registry import SessionRegistry
from ghostport.ui.settings_dialog import SettingsDialog
from ghostport.ui.shortcuts import install_shortcuts
from ghostport.ui.split_workspace import SessionPane, SplitWorkspace
from ghostport.ui.ssh_tab import SshTab, state_tab_icon
from ghostport.ui.theme import apply_theme, load_app_icon
from shiboken6 import isValid

log = logging.getLogger("ghostport.ui")


class MainWindow(QMainWindow):
    def __init__(self, app: GhostportApp) -> None:
        super().__init__()
        self._app = app
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)
        icon = load_app_icon()
        if icon is not None:
            self.setWindowIcon(icon)

        self.workspace = SplitWorkspace(self, home_locked=True)
        self.tabs = self.workspace.tabs

        self.home_btn = QPushButton("Home")
        self.home_btn.setObjectName("chromeButton")
        self.home_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.home_btn.clicked.connect(self.focus_home)
        self.sftp_btn = QPushButton("SFTP")
        self.sftp_btn.setObjectName("primaryButton")
        self.sftp_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sftp_btn.setToolTip("Open an SFTP session for the current SSH tab")
        self.sftp_btn.clicked.connect(self.open_sftp_current)
        hint = QLabel(f"Drag a tab to an edge to split  ·  drag the divider to resize  ·  max {MAX_SPLIT_PANES} panes")
        hint.setStyleSheet("color: #6b6b6b; font-size: 11px;")
        chrome = QWidget()
        chrome.setFixedHeight(40)
        row = QHBoxLayout(chrome)
        row.setContentsMargins(10, 4, 10, 4)
        row.addWidget(self.home_btn)
        row.addWidget(hint, 1)
        row.addWidget(self.sftp_btn)

        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(chrome)
        shell_layout.addWidget(self.workspace, 1)

        self.home = HomePage(app)
        home_index = self.tabs.addTab(self.home, "Home")
        self.tabs.tabBar().setTabButton(home_index, QTabBar.ButtonPosition.RightSide, None)

        self.sessions = SessionRegistry(app, self)
        self.setCentralWidget(shell)
        install_shortcuts(self, self.home)
        self.home.ssh_requested.connect(self.open_ssh)
        self.home.sftp_requested.connect(lambda pid: self.sessions.open_sftp(pid))
        self.workspace.tab_close_requested.connect(self._close_widget)
        self.workspace.current_widget_changed.connect(self._on_current_widget)
        self.workspace.tab_menu_requested.connect(self._tab_context_menu)
        app.connections.host_key_decision_needed.connect(self.on_host_key_prompt)
        app.installEventFilter(self)
        self._sync_sftp_button()
        self._warn_if_corrupt()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                from ghostport.ui.widgets.terminal_widget import TerminalWidget

                focus = self._app.focusWidget()
                if isinstance(focus, TerminalWidget) and focus._interactive:
                    focus.keyPressEvent(event)
                    return True
        return super().eventFilter(watched, event)

    def _warn_if_corrupt(self) -> None:
        warnings = [
            msg
            for msg in (self._app.profiles.load_warning, self._app.settings.load_warning)
            if msg
        ]
        if not warnings:
            return
        QMessageBox.warning(self, "Recovered configuration", "\n\n".join(warnings))

    def on_host_key_prompt(self, prompt: HostKeyPrompt) -> None:
        dialog = HostKeyDialog(prompt, self)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        if dialog.exec() == HostKeyDialog.DialogCode.Accepted:
            decision = dialog.decision()
        else:
            decision = HostKeyDecision.ABORT
        self._app.connections.provide_host_key_decision(prompt.prompt_id, decision)

    def open_ssh(self, profile_id: str, *, initial_directory: str | None = None) -> SshTab | None:
        try:
            profile = self._app.profiles.get(profile_id)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot connect", str(exc))
            return None
        session = self._app.connections.start_ssh(
            profile_id,
            initial_directory=initial_directory,
            parent=self,
        )
        pane = self.workspace.live_pane()
        if pane is None:
            QMessageBox.warning(self, "Cannot connect", "The session area is not available.")
            return None
        tab = SshTab(profile, session, self._app.settings.get(), parent=pane.tabs)
        self.sessions.register_ssh(tab)
        index = pane.tabs.addTab(tab, tab.display_title())
        pane.tabs.setTabIcon(index, state_tab_icon(session.state))
        pane.tabs.setCurrentIndex(index)
        tab.state_icon_changed.connect(lambda st, t=tab: self._update_tab_state(t, st))
        tab.close_requested.connect(lambda t=tab: self._close_ssh_widget(t))
        tab.reconnect_requested.connect(session.reconnect)
        tab.open_sftp_requested.connect(lambda t=tab: self.sessions.open_sftp(t.profile.id, remote_cwd=t.guess_remote_cwd()))
        tab.edit_server_requested.connect(self.home.edit_server)
        tab.title_changed.connect(lambda _n, t=tab: self._relabel(t))
        session.error_occurred.connect(lambda err, t=tab: self._on_ssh_error(t, err))
        session.thread_finished.connect(lambda t=tab: self._ssh_thread_done(t))
        self._sync_sftp_button()
        log.info("Opened SSH tab for %s", profile.name)
        return tab

    def _locate(self, tab: QWidget) -> tuple[SessionPane, int] | None:
        return self.workspace.locate(tab)

    def _update_tab_state(self, tab: SshTab, state: ConnectionState) -> None:
        found = self._locate(tab)
        if found is None:
            return
        pane, index = found
        pane.tabs.setTabIcon(index, state_tab_icon(state))
        pane.tabs.setTabText(index, tab.display_title())

    def _relabel(self, tab: SshTab) -> None:
        found = self._locate(tab)
        if found is None:
            return
        pane, index = found
        pane.tabs.setTabText(index, tab.display_title())

    def _on_ssh_error(self, tab: SshTab, error: GhostportError) -> None:
        if isinstance(error, HostKeyAborted):
            return
        dialog = ConnectionErrorDialog(error, self)
        if dialog.exec() != ConnectionErrorDialog.DialogCode.Accepted:
            return
        if dialog.choice() == ConnectionErrorDialog.RETRY:
            tab.session.reconnect()
        elif dialog.choice() == ConnectionErrorDialog.EDIT:
            self.home.edit_server(tab.profile.id)

    def _close_widget(self, widget: QWidget) -> None:
        if isinstance(widget, SshTab):
            self._close_ssh_widget(widget)

    def _close_ssh_widget(self, tab: SshTab) -> None:
        found = self._locate(tab)
        if found is not None:
            pane, index = found
            pane.tabs.removeTab(index)
            self.workspace._collapse_empty(pane)
        tab.hide()
        tab.setParent(self)
        tab.shutdown()
        self._sync_sftp_button()

    def _ssh_thread_done(self, tab: SshTab) -> None:
        if tab.session._restart:
            return
        if self._locate(tab) is not None:
            return
        self.sessions.unregister_ssh(tab.tab_id)
        tab.deleteLater()

    def _on_current_widget(self, widget: object) -> None:
        self._sync_sftp_button()
        if widget is None or getattr(widget, "is_home_page", False):
            self.setWindowTitle(APP_NAME)
            return
        if isinstance(widget, SshTab):
            self.setWindowTitle(f"{widget.display_title()} — {APP_NAME}")
            return
        self.setWindowTitle(APP_NAME)

    def _sync_sftp_button(self) -> None:
        tab = self.current_ssh_tab()
        self.sftp_btn.setEnabled(tab is not None)
        if tab is None:
            self.sftp_btn.setToolTip("Select an SSH tab, then open SFTP")
        else:
            self.sftp_btn.setToolTip(f"Open SFTP for {tab.profile.name}")

    def _tab_context_menu(self, pane: SessionPane, pos) -> None:
        index = pane.tabs.tabBar().tabAt(pos)
        if index < 0:
            return
        tab = pane.tabs.widget(index)
        if not isinstance(tab, SshTab):
            return
        menu = QMenu(self)
        rec = menu.addAction("Reconnect")
        disc = menu.addAction("Disconnect")
        dup = menu.addAction("Duplicate Tab")
        rename = menu.addAction("Rename Tab")
        menu.addSeparator()
        close = menu.addAction("Close Tab")
        close_others = menu.addAction("Close Other Tabs")
        chosen = menu.exec(pane.tabs.tabBar().mapToGlobal(pos))
        if chosen is rec:
            tab.session.reconnect()
        elif chosen is disc:
            tab.session.disconnect()
        elif chosen is dup:
            self.open_ssh(tab.profile.id)
        elif chosen is rename:
            text, ok = QInputDialog.getText(self, "Rename tab", "Name:", text=tab.display_title())
            if ok and text.strip():
                tab.title_override = text.strip()
                self._relabel(tab)
        elif chosen is close:
            self._close_ssh_widget(tab)
        elif chosen is close_others:
            for widget in list(self.workspace.iter_widgets()):
                if widget is not tab and isinstance(widget, SshTab):
                    self._close_ssh_widget(widget)

    def current_ssh_tab(self) -> SshTab | None:
        pane = self.workspace.live_pane()
        if pane is None:
            return None
        current = pane.tabs.currentWidget()
        if isinstance(current, SshTab):
            return current
        for widget in self.workspace.iter_widgets():
            if isinstance(widget, SshTab):
                return widget
        return None

    def duplicate_current_ssh(self) -> None:
        tab = self.current_ssh_tab()
        if tab is not None:
            self.open_ssh(tab.profile.id)

    def reconnect_current(self) -> None:
        tab = self.current_ssh_tab()
        if tab is not None:
            tab.session.reconnect()

    def open_sftp_current(self) -> None:
        tab = self.current_ssh_tab()
        if tab is not None:
            self.sessions.open_sftp(tab.profile.id, remote_cwd=tab.guess_remote_cwd())
            return
        pid = self.home.selected_profile_id()
        if pid:
            self.sessions.open_sftp(pid)

    def find_in_terminal(self) -> None:
        tab = self.current_ssh_tab()
        if tab is not None:
            tab.show_find()

    def copy_terminal(self) -> None:
        tab = self.current_ssh_tab()
        if tab is not None:
            tab.terminal.copy_selection()

    def paste_terminal(self) -> None:
        tab = self.current_ssh_tab()
        if tab is not None:
            tab.terminal.paste_clipboard()

    def focus_home(self) -> None:
        if not isValid(self.home):
            log.warning("Home page is gone; ignoring Home")
            return
        found = self.workspace.locate(self.home)
        if found is None:
            return
        pane, index = found
        pane.tabs.setCurrentIndex(index)
        self.workspace.set_active(pane)
        self.home.focus_search()

    def focus_search(self) -> None:
        self.focus_home()

    def new_tab_or_search(self) -> None:
        pane = self.workspace.live_pane()
        if pane is None:
            return
        current = pane.tabs.currentWidget()
        if current is not self.home:
            self.focus_home()
            return
        selected = self.home.selected_profile_id()
        if selected:
            self.open_ssh(selected)
            return
        self.home.focus_search()

    def close_current_closable_tab(self) -> None:
        pane = self.workspace.live_pane()
        if pane is None:
            return
        widget = pane.tabs.currentWidget()
        if isinstance(widget, SshTab):
            self._close_ssh_widget(widget)

    def next_tab(self) -> None:
        pane = self.workspace.live_pane()
        if pane is None:
            return
        tabs = pane.tabs
        count = tabs.count()
        if count <= 1:
            return
        tabs.setCurrentIndex((tabs.currentIndex() + 1) % count)

    def previous_tab(self) -> None:
        pane = self.workspace.live_pane()
        if pane is None:
            return
        tabs = pane.tabs
        count = tabs.count()
        if count <= 1:
            return
        tabs.setCurrentIndex((tabs.currentIndex() - 1) % count)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._app.settings.get(), self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        result = dialog.result_settings()
        if result is None:
            return
        self._app.settings.save(result)
        apply_theme(self._app, result)
        log.info("Settings saved theme=%s", result.appearance.theme)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        for widget in list(self.workspace.iter_widgets()):
            if isinstance(widget, SshTab):
                widget.shutdown()
        super().closeEvent(event)
