from __future__ import annotations

import logging
import sys

from ghostport.app import GhostportApp
from ghostport.config.paths import AppPaths
from ghostport.config.settings import SettingsStore
from ghostport.logging_setup import configure_logging
from ghostport.profiles.manager import ProfileManager
from ghostport.security.credentials import CredentialStore
from ghostport.services.connection_service import ConnectionService
from ghostport.ui.main_window import MainWindow
from ghostport.ui.theme import apply_theme, load_app_icon


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if any(flag in argv[1:] for flag in ("--setup", "--install", "--wizard")):
        from ghostport.ui.install_wizard import run_wizard

        return run_wizard(argv)
    app = GhostportApp(argv)
    app.paths = AppPaths.resolve()
    app.paths.ensure_dirs()
    configure_logging(app.paths)
    log = logging.getLogger("ghostport")
    log.info("Starting Ghostport (portable=%s root=%s)", app.paths.is_portable, app.paths.root)
    app.settings = SettingsStore(app.paths)
    app.settings.load()
    app.credentials = CredentialStore.for_paths(app.paths)
    app.profiles = ProfileManager(app.paths)
    app.profiles.load()
    app.connections = ConnectionService(
        app.profiles,
        app.credentials,
        app.paths,
        app.settings,
        parent=app,
    )
    from ghostport.sftp.transfer_manager import TransferManager

    app.transfers = TransferManager(
        app.profiles,
        app.credentials,
        app.connections.known_hosts,
        app.connections.host_key_gate,
        app.settings,
        on_host_key=app.connections._on_host_key_prompt,
        parent=app,
    )
    apply_theme(app, app.settings.get())
    icon = load_app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    window = MainWindow(app)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
