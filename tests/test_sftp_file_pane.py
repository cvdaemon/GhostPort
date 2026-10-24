from __future__ import annotations

import os

import pytest

from ghostport.config.settings import Settings, SettingsStore


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ghostport.app import GhostportApp

    existing = GhostportApp.instance()
    if existing is not None:
        yield existing
        return
    app = GhostportApp(["ghostport-test"])
    yield app
    app.quit()


def test_column_prefs_round_trip(portable_paths) -> None:
    store = SettingsStore(portable_paths)
    store.load()
    settings = store.get()
    settings.sftp.remote_column_widths = {"name": 320, "size": 88}
    settings.sftp.remote_hidden_columns = ["permissions"]
    settings.sftp.local_column_widths = {"name": 240}
    settings.sftp.local_hidden_columns = ["type"]
    store.save(settings)

    loaded = SettingsStore(portable_paths)
    result = loaded.load()
    assert result.sftp.remote_column_widths == {"name": 320, "size": 88}
    assert result.sftp.remote_hidden_columns == ["permissions"]
    assert result.sftp.local_column_widths == {"name": 240}
    assert result.sftp.local_hidden_columns == ["type"]


def test_column_prefs_sanitize_bad_json() -> None:
    settings = Settings.from_dict(
        {
            "sftp": {
                "remote_column_widths": {"name": "280", "size": -4, "nope": "x"},
                "remote_hidden_columns": ["permissions", 1, None],
                "local_column_widths": None,
            }
        }
    )
    assert settings.sftp.remote_column_widths == {"name": 280}
    assert settings.sftp.remote_hidden_columns == ["permissions"]
    assert settings.sftp.local_column_widths == {}


def test_name_column_is_interactive(qapp) -> None:
    from PySide6.QtWidgets import QHeaderView

    from ghostport.ui.widgets.file_pane import FilePane

    pane = FilePane("REMOTE", remote=True)
    header = pane.view.horizontalHeader()
    assert header.stretchLastSection() is False
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive
    assert header.sectionSize(0) >= 56
    pane.close()


def test_header_column_visibility_and_widths_persist(qapp, portable_paths) -> None:
    from ghostport.ui.widgets.file_pane import FilePane

    store = SettingsStore(portable_paths)
    store.load()
    pane = FilePane("REMOTE", remote=True, settings_store=store)
    header = pane.view.horizontalHeader()
    header.resizeSection(0, 333)
    pane.set_column_visible("permissions", False)
    pane._flush_columns()

    assert header.isSectionHidden(4)
    assert store.get().sftp.remote_column_widths["name"] == 333
    assert "permissions" in store.get().sftp.remote_hidden_columns
    pane.close()

    pane2 = FilePane("REMOTE", remote=True, settings_store=store)
    header2 = pane2.view.horizontalHeader()
    assert header2.sectionSize(0) == 333
    assert header2.isSectionHidden(4)
    assert not header2.isSectionHidden(0)
    pane2.set_column_visible("name", False)
    assert not header2.isSectionHidden(0)
    pane2.close()


def test_closing_ssh_window_leaves_sftp_open(qapp, portable_paths) -> None:
    from ghostport.profiles.manager import ProfileManager
    from ghostport.security.credentials import CredentialStore
    from ghostport.services.connection_service import ConnectionService
    from ghostport.ui.main_window import MainWindow
    from ghostport.ui.sftp_window import SftpWindow
    from ghostport.ui.theme import apply_theme

    qapp.paths = portable_paths
    qapp.settings = SettingsStore(portable_paths)
    qapp.settings.load()
    settings = qapp.settings.get()
    settings.appearance.dont_show_portable_secret_warning = True
    qapp.settings.save(settings)
    qapp.credentials = CredentialStore.for_paths(portable_paths)
    qapp.profiles = ProfileManager(portable_paths)
    qapp.profiles.load()
    qapp.connections = ConnectionService(
        qapp.profiles, qapp.credentials, qapp.paths, qapp.settings, parent=qapp
    )
    apply_theme(qapp, qapp.settings.get())

    main = MainWindow(qapp)
    sftp = SftpWindow(qapp, primary=True, parent=None)
    main.sessions.primary_sftp_window = sftp
    sftp.show()
    main.show()
    main.close()

    assert not sftp.isHidden()
    assert main.sessions.primary_sftp_window is sftp
    sftp.close()
    main.deleteLater()


def test_transfers_tab_created_once(qapp, portable_paths) -> None:
    from ghostport.profiles.manager import ProfileManager
    from ghostport.security.credentials import CredentialStore
    from ghostport.sftp.transfer_manager import TransferManager
    from ghostport.ui.sftp_window import SftpWindow
    from ghostport.ui.theme import apply_theme
    from ghostport.ui.widgets.transfer_queue import TransferQueueView

    qapp.paths = portable_paths
    qapp.settings = SettingsStore(portable_paths)
    qapp.settings.load()
    qapp.credentials = CredentialStore.for_paths(portable_paths)
    qapp.profiles = ProfileManager(portable_paths)
    qapp.profiles.load()
    from ghostport.ssh.known_hosts import HostKeyGate, KnownHostsStore

    qapp.transfers = TransferManager(
        qapp.profiles,
        qapp.credentials,
        KnownHostsStore(portable_paths.known_hosts_file),
        HostKeyGate(),
        qapp.settings,
        on_host_key=lambda _p: None,
        parent=qapp,
    )
    apply_theme(qapp, qapp.settings.get())
    window = SftpWindow(qapp, primary=True, parent=None)
    window._ensure_transfers_tab()
    window._ensure_transfers_tab()
    window._ensure_transfers_tab()
    views = [w for w in window.workspace.iter_widgets() if isinstance(w, TransferQueueView)]
    assert len(views) == 1
    window.close()
    window.deleteLater()
