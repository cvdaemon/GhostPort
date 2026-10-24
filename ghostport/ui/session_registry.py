from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject

from ghostport.app import GhostportApp
from ghostport.ssh.session import SshSession
from ghostport.ui.sftp_window import SftpWindow
from ghostport.ui.ssh_tab import SshTab


@dataclass
class SshEntry:
    tab_id: int
    profile_id: str
    session: SshSession
    tab: SshTab


class SessionRegistry(QObject):
    """Owned by MainWindow. May construct SshTab / SftpWindow."""

    def __init__(self, app: GhostportApp, main_window, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._main = main_window
        self._ssh: dict[int, SshEntry] = {}
        self.primary_sftp_window: SftpWindow | None = None
        self.extra_sftp_windows: list[SftpWindow] = []

    def register_ssh(self, tab: SshTab) -> None:
        self._ssh[tab.tab_id] = SshEntry(
            tab_id=tab.tab_id,
            profile_id=tab.profile.id,
            session=tab.session,
            tab=tab,
        )

    def unregister_ssh(self, tab_id: int) -> None:
        self._ssh.pop(tab_id, None)

    def find_connected_ssh(self, profile_id: str) -> int | None:
        from ghostport.ssh.session import ConnectionState

        for entry in self._ssh.values():
            if entry.profile_id == profile_id and entry.session.state is ConnectionState.CONNECTED:
                return entry.tab_id
        return None

    def ssh_tabs_for_profile(self, profile_id: str) -> list[int]:
        return [e.tab_id for e in self._ssh.values() if e.profile_id == profile_id]

    def entry(self, tab_id: int) -> SshEntry | None:
        return self._ssh.get(tab_id)

    def open_sftp(
        self,
        profile_id: str,
        *,
        new_window: bool = False,
        remote_cwd: str | None = None,
    ) -> None:
        from PySide6.QtWidgets import QMessageBox

        from ghostport.errors import ProfileNotFound

        try:
            self._app.profiles.get(profile_id)
        except ProfileNotFound as exc:
            QMessageBox.warning(self._main, "Cannot open SFTP", str(exc))
            return
        open_ssh = getattr(self._main, "open_ssh", None)
        if new_window:
            window = SftpWindow(
                self._app,
                primary=False,
                on_closed=self._extra_closed,
                on_open_ssh=open_ssh,
                parent=None,
            )
            self.extra_sftp_windows.append(window)
            window.add_tab(profile_id, force_new=True, remote_cwd=remote_cwd)
            return
        if self.primary_sftp_window is None:
            self.primary_sftp_window = SftpWindow(
                self._app,
                primary=True,
                on_closed=self._primary_closed,
                on_open_ssh=open_ssh,
                parent=None,
            )
        self.primary_sftp_window.add_tab(profile_id, force_new=False, remote_cwd=remote_cwd)

    def _primary_closed(self, window: SftpWindow) -> None:
        if self.primary_sftp_window is window:
            self.primary_sftp_window = None

    def _extra_closed(self, window: SftpWindow) -> None:
        self.extra_sftp_windows = [w for w in self.extra_sftp_windows if w is not window]
