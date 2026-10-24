from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ghostport.profiles.models import ServerProfile
from ghostport.ui.widgets.status_dot import ConnectionState, StatusDot


class ServerCard(QFrame):
    ssh_requested = Signal(str)
    sftp_requested = Signal(str)
    edit_requested = Signal(str)
    duplicate_requested = Signal(str)
    delete_requested = Signal(str)
    toggle_favorite_requested = Signal(str)
    activated = Signal(str)

    def __init__(self, profile: ServerProfile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.profile_id = profile.id
        self._favorite = profile.favorite
        self.setObjectName("serverCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(118)
        self.setStyleSheet(
            """
            QFrame#serverCard {
                background-color: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 8px;
            }
            QFrame#serverCard:hover {
                border-color: #3d9b94;
            }
            """
        )
        self._build(profile)

    def _build(self, profile: ServerProfile) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        title_row = QHBoxLayout()
        star = QLabel("★" if profile.favorite else "☆")
        star.setStyleSheet("color: #c9a227; font-size: 14px;")
        name = QLabel(profile.name)
        name.setStyleSheet("font-size: 15px; font-weight: 600;")
        title_row.addWidget(star)
        title_row.addWidget(name)
        title_row.addStretch(1)
        root.addLayout(title_row)

        target = QLabel(profile.display_target())
        target.setStyleSheet("color: #9d9d9d;")
        root.addWidget(target)

        cred_row = QHBoxLayout()
        cred_row.setSpacing(6)
        if profile.secret_ref:
            cred_row.addWidget(StatusDot(ConnectionState.SAVED))
            cred = QLabel("Saved credentials")
            cred.setStyleSheet("color: #9d9d9d; font-size: 12px;")
            cred_row.addWidget(cred)
        elif profile.auth_method.value == "agent":
            cred = QLabel("SSH agent")
            cred.setStyleSheet("color: #9d9d9d; font-size: 12px;")
            cred_row.addWidget(cred)
        elif profile.auth_method.value == "private_key":
            cred = QLabel("Private key")
            cred.setStyleSheet("color: #9d9d9d; font-size: 12px;")
            cred_row.addWidget(cred)
        cred_row.addStretch(1)
        root.addLayout(cred_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        ssh = QPushButton("SSH")
        ssh.setObjectName("primaryButton")
        sftp = QPushButton("SFTP")
        buttons.addWidget(ssh)
        buttons.addWidget(sftp)
        buttons.addStretch(1)
        root.addLayout(buttons)

        ssh.clicked.connect(lambda: self.ssh_requested.emit(self.profile_id))
        sftp.clicked.connect(lambda: self.sftp_requested.emit(self.profile_id))

    def sizeHint(self) -> QSize:
        return QSize(420, 128)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.activated.emit(self.profile_id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        menu = QMenu(self)
        ssh_act = menu.addAction("Connect SSH")
        sftp_act = menu.addAction("Open SFTP")
        menu.addSeparator()
        edit_act = menu.addAction("Edit")
        dup_act = menu.addAction("Duplicate")
        fav_act = menu.addAction(
            "Remove from Favorites" if self._favorite else "Add to Favorites"
        )
        menu.addSeparator()
        delete_act = menu.addAction("Delete Profile")
        chosen = menu.exec(event.globalPos())
        if chosen is edit_act:
            self.edit_requested.emit(self.profile_id)
        elif chosen is dup_act:
            self.duplicate_requested.emit(self.profile_id)
        elif chosen is fav_act:
            self.toggle_favorite_requested.emit(self.profile_id)
        elif chosen is delete_act:
            self.delete_requested.emit(self.profile_id)
        elif chosen is ssh_act:
            self.ssh_requested.emit(self.profile_id)
        elif chosen is sftp_act:
            self.sftp_requested.emit(self.profile_id)
