from __future__ import annotations

import os

import pytest

from ghostport.config.paths import AppPaths
from ghostport.config.settings import SettingsStore
from ghostport.profiles.manager import ProfileManager
from ghostport.profiles.models import AuthMethod
from ghostport.security.credentials import CredentialStore
from ghostport.ui.server_dialog import ServerDialogResult


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


def test_home_vault_round_trip(qapp, portable_paths: AppPaths) -> None:
    from ghostport.ui.home_page import HomePage
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
    apply_theme(qapp, qapp.settings.get())

    home = HomePage(qapp)
    result = ServerDialogResult(
        name="QA_SERVER",
        host="15.206.101.4",
        port=22,
        username="simtra",
        description="",
        auth_method=AuthMethod.PASSWORD,
        private_key_path=None,
        password="not-logged",
        passphrase="",
        favorite=True,
        keep_alive=True,
        keep_alive_interval_sec=30,
        timeout_sec=10,
        term_type="xterm-256color",
        initial_directory="/home/simtra",
    )
    profile = home.save_server(result, existing=None)
    assert profile.secret_ref
    assert qapp.credentials.get_secret(profile.secret_ref) == "not-logged"
    text = portable_paths.profiles_file.read_text(encoding="utf-8")
    assert "not-logged" not in text
    assert profile.secret_ref in text

    home.duplicate_profile(profile.id)
    clones = [p for p in qapp.profiles.all() if p.name.endswith("copy")]
    assert len(clones) == 1
    assert clones[0].secret_ref != profile.secret_ref
    assert qapp.credentials.get_secret(clones[0].secret_ref) == "not-logged"

    qapp.credentials.delete(profile.secret_ref)
    qapp.profiles.delete(profile.id)
    assert qapp.credentials.get_secret(clones[0].secret_ref) == "not-logged"


def test_create_rejects_empty_password(qapp, portable_paths: AppPaths) -> None:
    from ghostport.errors import ProfileValidationError
    from ghostport.ui.home_page import HomePage
    from ghostport.ui.theme import apply_theme

    qapp.paths = portable_paths
    qapp.settings = SettingsStore(portable_paths)
    qapp.settings.load()
    s = qapp.settings.get()
    s.appearance.dont_show_portable_secret_warning = True
    qapp.settings.save(s)
    qapp.credentials = CredentialStore.for_paths(portable_paths)
    qapp.profiles = ProfileManager(portable_paths)
    qapp.profiles.load()
    apply_theme(qapp, qapp.settings.get())
    home = HomePage(qapp)
    result = ServerDialogResult(
        name="X",
        host="1.2.3.4",
        port=22,
        username="u",
        description="",
        auth_method=AuthMethod.PASSWORD,
        private_key_path=None,
        password="",
        passphrase="",
        favorite=True,
        keep_alive=True,
        keep_alive_interval_sec=30,
        timeout_sec=10,
        term_type="xterm-256color",
        initial_directory="",
    )
    with pytest.raises(ProfileValidationError):
        home.save_server(result, existing=None)


def test_main_window_constructs(qapp, portable_paths: AppPaths) -> None:
    from ghostport.config.settings import SettingsStore
    from ghostport.profiles.manager import ProfileManager
    from ghostport.security.credentials import CredentialStore
    from ghostport.services.connection_service import ConnectionService
    from ghostport.ui.main_window import MainWindow
    from ghostport.ui.theme import apply_theme

    qapp.paths = portable_paths
    qapp.settings = SettingsStore(portable_paths)
    qapp.settings.load()
    qapp.credentials = CredentialStore.for_paths(portable_paths)
    qapp.profiles = ProfileManager(portable_paths)
    qapp.profiles.load()
    qapp.connections = ConnectionService(
        qapp.profiles, qapp.credentials, qapp.paths, qapp.settings, parent=qapp
    )
    apply_theme(qapp, qapp.settings.get())
    window = MainWindow(qapp)
    window.show()
    qapp.processEvents()
    assert window.tabs.count() == 1
    assert window.tabs.tabText(0) == "Home"
    window.close()


def test_terminal_default_fg_not_used_as_bg(qapp) -> None:
    from ghostport.ui.widgets.terminal_widget import TERM_BG, TERM_FG, TerminalWidget

    term = TerminalWidget()
    fg = term._cached_fg("default", False)
    bg = term._cached_bg("default")
    assert fg.getRgb()[:3] == TERM_FG.getRgb()[:3]
    assert bg.getRgb()[:3] == TERM_BG.getRgb()[:3]
    assert fg.getRgb()[:3] != bg.getRgb()[:3]
    term.deleteLater()


def test_terminal_ansi_blue_readable_on_dark(qapp) -> None:
    from ghostport.ui.widgets.terminal_widget import (
        _MIN_FG_LUMA,
        _luma,
        TERM_BG,
        ensure_readable_fg,
        resolve_color,
        TerminalWidget,
    )

    blue = resolve_color("blue", default=TERM_BG)
    bold_blue = resolve_color("blue", default=TERM_BG, bold=True)
    hex_navy = ensure_readable_fg(resolve_color("0000ee", default=TERM_BG))
    assert blue.name().lower() != "#0000ee"
    assert _luma(blue) >= _MIN_FG_LUMA - 1
    assert _luma(bold_blue) >= _MIN_FG_LUMA - 1
    assert _luma(hex_navy) >= _MIN_FG_LUMA - 1

    term = TerminalWidget()
    painted = term._cached_fg("blue", True)
    assert _luma(painted) >= _MIN_FG_LUMA - 1
    bg_blue = term._cached_bg("blue")
    assert bg_blue.getRgb()[:3] == blue.getRgb()[:3]
    term.deleteLater()


def test_server_dialog_accept_coerces_auth(qapp) -> None:
    from ghostport.ui.server_dialog import ServerDialog

    dialog = ServerDialog(None)
    dialog.name.setText("QA_SERVER")
    dialog.host.setText("15.206.101.4")
    dialog.username.setText("simtra")
    dialog.password.setText("secret")
    dialog._on_accept()
    result = dialog.result_data()
    assert result is not None
    assert result.auth_method is AuthMethod.PASSWORD
    assert result.name == "QA_SERVER"
    dialog.deleteLater()
