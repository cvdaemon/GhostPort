from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QMainWindow, QWidget


def _bind(parent: QWidget, sequence: str, slot, context: Qt.ShortcutContext) -> None:
    shortcut = QShortcut(QKeySequence(sequence), parent)
    shortcut.setContext(context)
    shortcut.activated.connect(slot)


def install_shortcuts(window: QMainWindow, home: QWidget) -> None:
    """Home chrome uses WidgetWithChildren so a focused terminal keeps Ctrl+T/W/F."""
    child = Qt.ShortcutContext.WidgetWithChildrenShortcut
    window_ctx = Qt.ShortcutContext.WindowShortcut
    _bind(home, "Ctrl+F", window.focus_search, child)
    _bind(home, "Ctrl+T", window.new_tab_or_search, child)
    _bind(home, "Ctrl+W", window.close_current_closable_tab, child)
    _bind(window, "Ctrl+,", window.open_settings, window_ctx)
    _bind(window, "Ctrl+Tab", window.next_tab, window_ctx)
    _bind(window, "Ctrl+Shift+Tab", window.previous_tab, window_ctx)
    _bind(window, "Ctrl+Shift+T", window.duplicate_current_ssh, window_ctx)
    _bind(window, "Ctrl+Shift+W", window.close_current_closable_tab, window_ctx)
    _bind(window, "Ctrl+Shift+R", window.reconnect_current, window_ctx)
    _bind(window, "Ctrl+Shift+S", window.open_sftp_current, window_ctx)
    _bind(window, "Ctrl+Shift+F", window.find_in_terminal, window_ctx)
    _bind(window, "Ctrl+Insert", window.copy_terminal, window_ctx)
    _bind(window, "Shift+Insert", window.paste_terminal, window_ctx)
    _bind(window, "Ctrl+Shift+C", window.copy_terminal, window_ctx)
    _bind(window, "Ctrl+Shift+V", window.paste_terminal, window_ctx)
