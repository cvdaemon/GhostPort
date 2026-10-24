from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ghostport.errors import GhostportError


class ConnectionErrorDialog(QDialog):
    RETRY = 1
    EDIT = 2

    def __init__(self, error: GhostportError, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(error.user_title())
        self._choice = 0
        heading = QLabel(error.user_title())
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        body = QLabel(error.user_body())
        body.setWordWrap(True)
        buttons = QDialogButtonBox()
        retry = buttons.addButton("Retry", QDialogButtonBox.ButtonRole.AcceptRole)
        edit = buttons.addButton("Edit Server", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        retry.clicked.connect(lambda: self._done(self.RETRY))
        edit.clicked.connect(lambda: self._done(self.EDIT))
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)

    def _done(self, choice: int) -> None:
        self._choice = choice
        self.accept()

    def choice(self) -> int:
        return self._choice
