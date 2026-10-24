from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ghostport.ssh.known_hosts import HostKeyDecision, HostKeyKind, HostKeyPrompt


class HostKeyDialog(QDialog):
    """Unknown vs mismatch. Reject / Escape = ABORT. Application-modal."""

    def __init__(self, prompt: HostKeyPrompt, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SSH host key")
        self.setModal(True)
        self._prompt = prompt
        self._decision = HostKeyDecision.ABORT

        if prompt.kind is HostKeyKind.UNKNOWN:
            title = "Unknown host key"
            body = (
                f"Ghostport has not seen this server before.\n\n"
                f"Host: {prompt.host}\n"
                f"Port: {prompt.port}\n"
                f"Key type: {prompt.key_type}\n"
                f"Fingerprint: {prompt.fingerprint_sha256}\n\n"
                f"Trust this host key and continue?"
            )
            ok_label = "Trust and Continue"
            ok_name = "primaryButton"
        else:
            title = "Host key mismatch"
            body = (
                f"The host key for this server does not match what Ghostport stored.\n\n"
                f"This can mean the server was reinstalled — or a man-in-the-middle attack.\n\n"
                f"Host: {prompt.host}\n"
                f"Port: {prompt.port}\n\n"
                f"Stored: {prompt.stored_key_type or '?'}  {prompt.stored_fingerprint_sha256 or '?'}\n"
                f"Seen:   {prompt.key_type}  {prompt.fingerprint_sha256}\n\n"
                f"Replace the stored key and continue only if you expected this change."
            )
            ok_label = "Replace and Continue"
            ok_name = "dangerButton"

        heading = QLabel(title)
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        text = QLabel(body)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText(ok_label)
            ok.setObjectName(ok_name)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(text)
        layout.addWidget(buttons)
        self.setMinimumWidth(520)

    def _accept(self) -> None:
        if self._prompt.kind is HostKeyKind.UNKNOWN:
            self._decision = HostKeyDecision.TRUST
        else:
            self._decision = HostKeyDecision.REPLACE
        self.accept()

    def decision(self) -> HostKeyDecision:
        return self._decision

    def reject(self) -> None:
        self._decision = HostKeyDecision.ABORT
        super().reject()
