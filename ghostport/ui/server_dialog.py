from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    QVBoxLayout,
    QWidget,
)

from ghostport.constants import DEFAULT_PORT, DEFAULT_TERM
from ghostport.profiles.models import AuthMethod, ServerProfile, coerce_auth_method


@dataclass
class ServerDialogResult:
    name: str
    host: str
    port: int
    username: str
    description: str
    auth_method: AuthMethod
    private_key_path: str | None
    password: str
    passphrase: str
    favorite: bool
    keep_alive: bool
    keep_alive_interval_sec: int
    timeout_sec: int
    term_type: str
    initial_directory: str


class ServerDialog(QDialog):
    """Add/Edit form only. Returns field values. Does not write the vault."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        profile: ServerProfile | None = None,
        defaults_timeout: int = 10,
        defaults_keepalive: int = 30,
        defaults_term: str = DEFAULT_TERM,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self.setWindowTitle("Edit Server" if profile else "Add Server")
        self.setMinimumWidth(460)
        self._result: ServerDialogResult | None = None

        self.name = QLineEdit()
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(DEFAULT_PORT)
        self.username = QLineEdit()
        self.description = QLineEdit()
        self.auth = QComboBox()
        self.auth.addItem("Password", AuthMethod.PASSWORD.value)
        self.auth.addItem("Private Key", AuthMethod.PRIVATE_KEY.value)
        self.auth.addItem("SSH Agent", AuthMethod.AGENT.value)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Leave blank to keep existing" if profile else "")
        self.key_path = QLineEdit()
        self.browse = QPushButton("Browse")
        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase.setPlaceholderText("Leave blank to keep existing" if profile else "Optional")
        self.favorite = QCheckBox("Favorite")
        self.favorite.setChecked(True)
        self.keep_alive = QCheckBox("Keep alive")
        self.keep_alive.setChecked(True)
        self.keep_alive_interval = QSpinBox()
        self.keep_alive_interval.setRange(5, 600)
        self.keep_alive_interval.setValue(defaults_keepalive)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 120)
        self.timeout.setValue(defaults_timeout)
        self.term_type = QComboBox()
        self.term_type.addItems(["xterm-256color", "xterm", "vt100"])
        idx = self.term_type.findText(defaults_term)
        self.term_type.setCurrentIndex(idx if idx >= 0 else 0)
        self.initial_directory = QLineEdit()

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Host", self.host)
        form.addRow("Port", self.port)
        form.addRow("Username", self.username)
        form.addRow("Notes", self.description)
        form.addRow("Authentication", self.auth)
        form.addRow("Password", self.password)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_path)
        key_row.addWidget(self.browse)
        key_wrap = QWidget()
        key_wrap.setLayout(key_row)
        form.addRow("Private key", key_wrap)
        form.addRow("Passphrase", self.passphrase)
        form.addRow("", self.favorite)
        form.addRow("", self.keep_alive)
        form.addRow("Keep-alive interval (sec)", self.keep_alive_interval)
        form.addRow("Connection timeout (sec)", self.timeout)
        form.addRow("Terminal type", self.term_type)
        form.addRow("Initial directory", self.initial_directory)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setObjectName("primaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.auth.currentIndexChanged.connect(self._sync_auth_fields)
        self.browse.clicked.connect(self._browse_key)
        if profile is not None:
            self._load_profile(profile)
        self._sync_auth_fields()

    def _load_profile(self, profile: ServerProfile) -> None:
        self.name.setText(profile.name)
        self.host.setText(profile.host)
        self.port.setValue(profile.port)
        self.username.setText(profile.username)
        self.description.setText(profile.description)
        idx = self.auth.findData(profile.auth_method.value)
        if idx >= 0:
            self.auth.setCurrentIndex(idx)
        if profile.private_key_path:
            self.key_path.setText(profile.private_key_path)
        self.favorite.setChecked(profile.favorite)
        self.keep_alive.setChecked(profile.keep_alive)
        self.keep_alive_interval.setValue(profile.keep_alive_interval_sec)
        self.timeout.setValue(profile.timeout_sec)
        tidx = self.term_type.findText(profile.term_type)
        if tidx >= 0:
            self.term_type.setCurrentIndex(tidx)
        self.initial_directory.setText(profile.initial_directory)

    def _current_auth(self) -> AuthMethod:
        return coerce_auth_method(self.auth.currentData())

    def _sync_auth_fields(self) -> None:
        method = self._current_auth()
        is_password = method is AuthMethod.PASSWORD
        is_key = method is AuthMethod.PRIVATE_KEY
        self.password.setVisible(is_password)
        self.key_path.setVisible(is_key)
        self.browse.setVisible(is_key)
        self.passphrase.setVisible(is_key)
        form = self.findChild(QFormLayout)
        if form is None:
            return
        self._set_row_visible(form, self.password, is_password)
        self._set_row_visible(form, self.key_path.parentWidget(), is_key)
        self._set_row_visible(form, self.passphrase, is_key)

    def _set_row_visible(self, form: QFormLayout, field: QWidget | None, visible: bool) -> None:
        if field is None:
            return
        form.labelForField(field).setVisible(visible) if form.labelForField(field) else None
        field.setVisible(visible)

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select private key",
            str(Path.home() / ".ssh"),
            "Key files (*.pem *.key *);;All files (*.*)",
        )
        if path:
            self.key_path.setText(path)

    def result_data(self) -> ServerDialogResult | None:
        return self._result

    def _on_accept(self) -> None:
        method = self._current_auth()
        key_path = self.key_path.text().strip() or None
        self._result = ServerDialogResult(
            name=self.name.text().strip(),
            host=self.host.text().strip(),
            port=int(self.port.value()),
            username=self.username.text().strip(),
            description=self.description.text().strip(),
            auth_method=method,
            private_key_path=key_path,
            password=self.password.text(),
            passphrase=self.passphrase.text(),
            favorite=self.favorite.isChecked(),
            keep_alive=self.keep_alive.isChecked(),
            keep_alive_interval_sec=int(self.keep_alive_interval.value()),
            timeout_sec=int(self.timeout.value()),
            term_type=self.term_type.currentText(),
            initial_directory=self.initial_directory.text().strip(),
        )
        self.password.clear()
        self.passphrase.clear()
        self.accept()

    def reject(self) -> None:
        self.password.clear()
        self.passphrase.clear()
        super().reject()
