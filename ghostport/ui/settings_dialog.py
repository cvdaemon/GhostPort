from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ghostport.config.settings import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self._settings = settings
        self._result: Settings | None = None

        tabs = QTabWidget()
        tabs.addTab(self._appearance_tab(), "Appearance")
        tabs.addTab(self._terminal_tab(), "Terminal")
        tabs.addTab(self._connections_tab(), "Connections")
        tabs.addTab(self._sftp_tab(), "SFTP")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setObjectName("primaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def _appearance_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.theme = QComboBox()
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Light", "light")
        self.theme.addItem("System", "system")
        idx = self.theme.findData(self._settings.appearance.theme)
        self.theme.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Theme", self.theme)
        return page

    def _terminal_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.font_family = QLineEdit(self._settings.terminal.font_family)
        self.font_family.setPlaceholderText("Cascadia Mono / Consolas")
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 32)
        self.font_size.setValue(self._settings.terminal.font_size)
        self.cursor_style = QComboBox()
        self.cursor_style.addItems(["block", "bar", "underline"])
        cidx = self.cursor_style.findText(self._settings.terminal.cursor_style)
        self.cursor_style.setCurrentIndex(cidx if cidx >= 0 else 0)
        self.scrollback = QSpinBox()
        self.scrollback.setRange(200, 10000)
        self.scrollback.setValue(self._settings.terminal.scrollback_lines)
        self.scrollback.setToolTip("Oldest terminal lines are discarded past this rolling window.")
        self.copy_on_select = QCheckBox("Copy on select")
        self.copy_on_select.setChecked(self._settings.terminal.copy_on_select)
        self.paste_warning = QSpinBox()
        self.paste_warning.setRange(0, 100000)
        self.paste_warning.setValue(self._settings.terminal.paste_warning_threshold)
        self.term_type = QComboBox()
        self.term_type.addItems(["xterm-256color", "xterm", "vt100"])
        tidx = self.term_type.findText(self._settings.terminal.term_type)
        self.term_type.setCurrentIndex(tidx if tidx >= 0 else 0)
        form.addRow("Font", self.font_family)
        form.addRow("Font size", self.font_size)
        form.addRow("Cursor style", self.cursor_style)
        form.addRow("Scrollback lines", self.scrollback)
        form.addRow("", self.copy_on_select)
        form.addRow("Paste warning threshold", self.paste_warning)
        form.addRow("Terminal type", self.term_type)
        return page

    def _connections_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 120)
        self.timeout.setValue(self._settings.connections.timeout_sec)
        self.keepalive = QSpinBox()
        self.keepalive.setRange(5, 600)
        self.keepalive.setValue(self._settings.connections.keep_alive_interval_sec)
        self.reconnect_focus = QCheckBox("Reconnect when focusing a disconnected tab")
        self.reconnect_focus.setChecked(self._settings.connections.reconnect_on_tab_focus)
        self.idle_timeout = QSpinBox()
        self.idle_timeout.setRange(0, 24 * 60)
        self.idle_timeout.setSpecialValueText("Never")
        self.idle_timeout.setSuffix(" min")
        self.idle_timeout.setValue(self._settings.connections.idle_timeout_min)
        self.idle_timeout.setToolTip("Close SSH and SFTP tabs after this much idle time. No prompt.")
        form.addRow("Connection timeout (sec)", self.timeout)
        form.addRow("Keep-alive interval (sec)", self.keepalive)
        form.addRow("Close idle tabs after", self.idle_timeout)
        form.addRow("", self.reconnect_focus)
        return page

    def _sftp_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.default_local = QLineEdit(self._settings.sftp.default_local_directory)
        browse = QPushButton("Browse")
        row = QHBoxLayout()
        row.addWidget(self.default_local)
        row.addWidget(browse)
        wrap = QWidget()
        wrap.setLayout(row)
        browse.clicked.connect(self._browse_local)
        self.show_hidden = QCheckBox("Show hidden files")
        self.show_hidden.setChecked(self._settings.sftp.show_hidden_files)
        self.confirm_deletes = QCheckBox("Confirm deletes")
        self.confirm_deletes.setChecked(self._settings.sftp.confirm_deletes)
        self.confirm_overwrites = QCheckBox("Confirm overwrites")
        self.confirm_overwrites.setChecked(self._settings.sftp.confirm_overwrites)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 8)
        self.concurrency.setValue(self._settings.sftp.transfer_concurrency)
        form.addRow("Default local directory", wrap)
        form.addRow("", self.show_hidden)
        form.addRow("", self.confirm_deletes)
        form.addRow("", self.confirm_overwrites)
        form.addRow("Transfer concurrency", self.concurrency)
        return page

    def _browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Default local directory")
        if path:
            self.default_local.setText(path)

    def result_settings(self) -> Settings | None:
        return self._result

    def _on_accept(self) -> None:
        s = self._settings
        s.appearance.theme = str(self.theme.currentData())
        s.terminal.font_family = self.font_family.text().strip()
        s.terminal.font_size = int(self.font_size.value())
        s.terminal.cursor_style = self.cursor_style.currentText()
        s.terminal.scrollback_lines = int(self.scrollback.value())
        s.terminal.copy_on_select = self.copy_on_select.isChecked()
        s.terminal.paste_warning_threshold = int(self.paste_warning.value())
        s.terminal.term_type = self.term_type.currentText()
        s.connections.timeout_sec = int(self.timeout.value())
        s.connections.keep_alive_interval_sec = int(self.keepalive.value())
        s.connections.idle_timeout_min = int(self.idle_timeout.value())
        s.connections.reconnect_on_tab_focus = self.reconnect_focus.isChecked()
        s.sftp.default_local_directory = self.default_local.text().strip()
        s.sftp.show_hidden_files = self.show_hidden.isChecked()
        s.sftp.confirm_deletes = self.confirm_deletes.isChecked()
        s.sftp.confirm_overwrites = self.confirm_overwrites.isChecked()
        s.sftp.transfer_concurrency = int(self.concurrency.value())
        self._result = s
        self.accept()
