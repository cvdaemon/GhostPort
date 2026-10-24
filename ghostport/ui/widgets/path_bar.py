from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit, QWidget


class PathBar(QLineEdit):
    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.returnPressed.connect(self._submit)

    def _submit(self) -> None:
        self.submitted.emit(self.text().strip())
