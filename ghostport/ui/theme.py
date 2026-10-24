from __future__ import annotations

import logging
import sys
import winreg
from pathlib import Path
from string import Template

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ghostport.config.settings import Settings

log = logging.getLogger("ghostport.ui")

# tokens: bg, bg_panel, bg_elevated, border, text, text_muted, accent, danger, warn
DARK_TOKENS = {
    "bg": "#1e1e1e",
    "bg_panel": "#252526",
    "bg_elevated": "#2d2d2d",
    "border": "#3c3c3c",
    "text": "#d4d4d4",
    "text_muted": "#9d9d9d",
    "accent": "#3d9b94",
    "danger": "#c45454",
    "warn": "#c9a227",
}

LIGHT_TOKENS = {
    "bg": "#f3f3f3",
    "bg_panel": "#ffffff",
    "bg_elevated": "#ececec",
    "border": "#d0d0d0",
    "text": "#1e1e1e",
    "text_muted": "#6b6b6b",
    "accent": "#3d9b94",
    "danger": "#c45454",
    "warn": "#c9a227",
}

_STYLES_DIR = Path(__file__).resolve().parents[1] / "resources" / "styles"
_ICONS_DIR = Path(__file__).resolve().parents[1] / "resources" / "icons"


def windows_apps_use_light_theme() -> bool:
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 1
    except OSError:
        return False


def resolve_theme_name(settings: Settings) -> str:
    theme = (settings.appearance.theme or "dark").lower()
    if theme == "system":
        return "light" if windows_apps_use_light_theme() else "dark"
    if theme == "light":
        return "light"
    return "dark"


def load_qss(theme_name: str) -> str:
    filename = "light.qss" if theme_name == "light" else "dark.qss"
    path = _STYLES_DIR / filename
    return path.read_text(encoding="utf-8")


def apply_theme(app: QApplication, settings: Settings) -> None:
    """Substitute $bg / ${bg} via string.Template. Never str.format_map."""
    theme_name = resolve_theme_name(settings)
    raw = load_qss(theme_name)
    tokens = LIGHT_TOKENS if theme_name == "light" else DARK_TOKENS
    qss = Template(raw).safe_substitute(tokens)
    app.setStyleSheet(qss)


def load_app_icon() -> QIcon | None:
    svg = _ICONS_DIR / "app.svg"
    if not svg.is_file():
        return None
    return QIcon(str(svg))
