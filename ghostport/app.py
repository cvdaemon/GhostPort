from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from ghostport.config.paths import AppPaths
from ghostport.config.settings import SettingsStore
from ghostport.constants import APP_NAME
from ghostport.profiles.manager import ProfileManager
from ghostport.security.credentials import CredentialStore


def prepare_dpi() -> None:
    """Keep fractional Windows scaling (125%/150%) instead of stretching a 96-dpi bitmap."""
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )


class GhostportApp(QApplication):
    """PR2 attaches ConnectionService. TransferManager lands in PR5."""

    paths: AppPaths
    settings: SettingsStore
    profiles: ProfileManager
    credentials: CredentialStore
    connections: object
    transfers: object

    def __init__(self, argv: list[str]) -> None:
        prepare_dpi()
        super().__init__(argv)
        self.setApplicationName(APP_NAME)
        self.setOrganizationName(APP_NAME)
        self.setApplicationDisplayName(APP_NAME)
