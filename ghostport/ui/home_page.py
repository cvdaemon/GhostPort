from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ghostport.app import GhostportApp
from ghostport.constants import APP_NAME
from ghostport.errors import ProfileValidationError
from ghostport.profiles.models import AuthMethod, ServerProfile
from ghostport.ui.server_dialog import ServerDialog, ServerDialogResult
from ghostport.ui.widgets.server_card import ServerCard

log = logging.getLogger("ghostport.ui")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class HomePage(QWidget):
    """Favorites cards, search, Add Server. Owns the vault write path."""

    is_home_page = True
    profiles_changed = Signal()
    ssh_requested = Signal(str)
    sftp_requested = Signal(str)

    def __init__(self, app: GhostportApp, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._selected_id: str | None = None

        from ghostport import __version__
        from ghostport.constants import APP_YEAR
        from ghostport.ui.theme import load_app_icon

        hero = QHBoxLayout()
        hero.setSpacing(16)
        icon_label = QLabel()
        icon = load_app_icon()
        if icon is not None:
            icon_label.setPixmap(icon.pixmap(56, 56))
        else:
            icon_label.setText("👻")
            icon_label.setStyleSheet("font-size: 36px;")
        titles = QVBoxLayout()
        titles.setSpacing(2)
        name = QLabel(APP_NAME)
        name.setStyleSheet("font-size: 28px; font-weight: 700; letter-spacing: 0.5px;")
        tagline = QLabel("One click to your servers. SSH first, SFTP when you need a file.")
        tagline.setStyleSheet("color: #9d9d9d; font-size: 13px;")
        meta = QLabel(f"Version {__version__}  ·  {APP_YEAR}")
        meta.setStyleSheet("color: #6b6b6b; font-size: 12px;")
        titles.addWidget(name)
        titles.addWidget(tagline)
        titles.addWidget(meta)
        add_btn = QPushButton("+ Add Server")
        add_btn.setObjectName("primaryButton")
        settings_btn = QPushButton("⚙ Settings")
        install_btn = QPushButton("Install on this PC…")
        add_btn.clicked.connect(self.add_server)
        settings_btn.clicked.connect(self._open_settings)
        install_btn.clicked.connect(self._open_installer)
        actions = QVBoxLayout()
        actions.addStretch(1)
        actions.addWidget(add_btn)
        actions.addWidget(settings_btn)
        actions.addWidget(install_btn)
        actions.addStretch(1)
        hero.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        hero.addLayout(titles, 1)
        hero.addLayout(actions)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search servers...")
        self.search.textChanged.connect(self.refresh)

        fav_label = QLabel("YOUR SERVERS")
        fav_label.setStyleSheet("color: #9d9d9d; letter-spacing: 1.4px; font-size: 11px; font-weight: 600;")

        self.list = QListWidget()
        self.list.setSpacing(2)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.model().rowsMoved.connect(self._on_reordered)

        self.empty = QLabel("No servers yet. Add one and Ghostport will remember it.")
        self.empty.setStyleSheet("color: #9d9d9d;")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        rule = QLabel()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background-color: #3c3c3c;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)
        layout.addLayout(hero)
        layout.addWidget(rule)
        layout.addWidget(self.search)
        layout.addWidget(fav_label)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty)

        self.refresh()

    def focus_search(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def selected_profile_id(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return self._selected_id
        return item.data(Qt.ItemDataRole.UserRole)

    def add_server(self) -> None:
        settings = self._app.settings.get()
        dialog = ServerDialog(
            self,
            defaults_timeout=settings.connections.timeout_sec,
            defaults_keepalive=settings.connections.keep_alive_interval_sec,
            defaults_term=settings.terminal.term_type,
        )
        if dialog.exec() != ServerDialog.DialogCode.Accepted:
            return
        result = dialog.result_data()
        if result is None:
            return
        try:
            self.save_server(result, existing=None)
        except ProfileValidationError as exc:
            QMessageBox.warning(self, "Cannot save server", exc.reason or str(exc))

    def edit_server(self, profile_id: str) -> None:
        try:
            existing = self._app.profiles.get(profile_id)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot edit server", str(exc))
            return
        settings = self._app.settings.get()
        dialog = ServerDialog(
            self,
            profile=existing,
            defaults_timeout=settings.connections.timeout_sec,
            defaults_keepalive=settings.connections.keep_alive_interval_sec,
            defaults_term=settings.terminal.term_type,
        )
        if dialog.exec() != ServerDialog.DialogCode.Accepted:
            return
        result = dialog.result_data()
        if result is None:
            return
        try:
            self.save_server(result, existing=existing)
        except ProfileValidationError as exc:
            QMessageBox.warning(self, "Cannot save server", exc.reason or str(exc))

    def save_server(
        self,
        result: ServerDialogResult,
        *,
        existing: ServerProfile | None,
    ) -> ServerProfile:
        self._validate_form(result, is_create=existing is None)
        now = _now_iso()
        if existing is None:
            profile = ServerProfile(
                id=str(uuid.uuid4()),
                name=result.name,
                host=result.host,
                port=result.port,
                username=result.username,
                description=result.description,
                auth_method=result.auth_method,
                private_key_path=result.private_key_path,
                favorite=result.favorite,
                sort_index=len(self._app.profiles.all()),
                keep_alive=result.keep_alive,
                keep_alive_interval_sec=result.keep_alive_interval_sec,
                timeout_sec=result.timeout_sec,
                term_type=result.term_type,
                initial_directory=result.initial_directory,
                created_at=now,
                updated_at=now,
            )
            profile.secret_ref = self._mint_secret(result, old_ref=None)
        else:
            profile = existing
            profile.name = result.name
            profile.host = result.host
            profile.port = result.port
            profile.username = result.username
            profile.description = result.description
            profile.auth_method = result.auth_method
            profile.private_key_path = result.private_key_path
            profile.favorite = result.favorite
            profile.keep_alive = result.keep_alive
            profile.keep_alive_interval_sec = result.keep_alive_interval_sec
            profile.timeout_sec = result.timeout_sec
            profile.term_type = result.term_type
            profile.initial_directory = result.initial_directory
            profile.updated_at = now
            profile.secret_ref = self._apply_secret_edit(result, existing)

        if profile.secret_ref:
            self._maybe_warn_portable()
        self._app.profiles.upsert(profile)
        log.info("Saved profile %s host=%s port=%s auth=%s", profile.name, profile.host, profile.port, profile.auth_method.value)
        self.refresh()
        self.profiles_changed.emit()
        return profile

    def _validate_form(self, result: ServerDialogResult, *, is_create: bool) -> None:
        if not result.name:
            raise ProfileValidationError("Name is required", reason="Enter a profile name.")
        if not result.host:
            raise ProfileValidationError("Host is required", reason="Enter a hostname or IP address.")
        if not (1 <= result.port <= 65535):
            raise ProfileValidationError("Invalid port", reason="Port must be between 1 and 65535.")
        if not result.username:
            raise ProfileValidationError("Username is required", reason="Enter a username.")
        if result.auth_method is AuthMethod.PASSWORD and is_create and not result.password:
            raise ProfileValidationError(
                "Password is required",
                reason="Enter a password, or choose a different authentication method.",
            )
        if result.auth_method is AuthMethod.PRIVATE_KEY:
            if not result.private_key_path:
                raise ProfileValidationError(
                    "Private key is required",
                    reason="Choose a private key file.",
                )
            if not Path(result.private_key_path).is_file():
                raise ProfileValidationError(
                    "Private key not found",
                    reason=f"The key file does not exist:\n{result.private_key_path}",
                )

    def _mint_secret(self, result: ServerDialogResult, *, old_ref: str | None) -> str | None:
        store = self._app.credentials
        if result.auth_method is AuthMethod.PASSWORD:
            return store.put_secret(result.password, secret_ref=old_ref)
        if result.auth_method is AuthMethod.PRIVATE_KEY and result.passphrase:
            return store.put_secret(result.passphrase, secret_ref=old_ref)
        return None

    def _apply_secret_edit(self, result: ServerDialogResult, existing: ServerProfile) -> str | None:
        store = self._app.credentials
        old_ref = existing.secret_ref
        if result.auth_method is AuthMethod.AGENT:
            if old_ref:
                store.delete(old_ref)
            return None
        if result.auth_method is AuthMethod.PASSWORD:
            if result.password:
                return store.put_secret(result.password, secret_ref=old_ref)
            return old_ref
        # private_key
        if result.passphrase:
            return store.put_secret(result.passphrase, secret_ref=old_ref)
        if existing.auth_method is not AuthMethod.PRIVATE_KEY and old_ref:
            store.delete(old_ref)
            return None
        return old_ref if existing.auth_method is AuthMethod.PRIVATE_KEY else old_ref

    def _maybe_warn_portable(self) -> None:
        settings = self._app.settings.get()
        if not self._app.paths.is_portable:
            return
        if settings.appearance.dont_show_portable_secret_warning:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Portable credential storage")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            "Passwords are encrypted with Windows DPAPI for this Windows user on this PC.\n\n"
            "Copying Ghostport to a USB stick will not take usable passwords to another computer."
        )
        check = QCheckBox("Don't show again")
        box.setCheckBox(check)
        box.exec()
        if check.isChecked():
            settings.appearance.dont_show_portable_secret_warning = True
            self._app.settings.save(settings)

    def duplicate_profile(self, profile_id: str) -> None:
        src = self._app.profiles.get(profile_id)
        clone = deepcopy(src)
        clone.id = str(uuid.uuid4())
        clone.name = f"{src.name} copy"
        clone.created_at = _now_iso()
        clone.updated_at = clone.created_at
        clone.sort_index = len(self._app.profiles.all())
        if src.secret_ref:
            secret = self._app.credentials.get_secret(src.secret_ref)
            if secret is None:
                clone.secret_ref = None
            else:
                clone.secret_ref = self._app.credentials.put_secret(secret)
        self._app.profiles.upsert(clone)
        log.info("Duplicated profile %s -> %s", src.name, clone.name)
        self.refresh()
        self.profiles_changed.emit()

    def delete_profile(self, profile_id: str) -> None:
        profile = self._app.profiles.get(profile_id)
        confirm = QMessageBox.question(
            self,
            "Delete profile",
            f"Delete {profile.name}?\nThis also removes saved credentials for this server.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if profile.secret_ref:
            self._app.credentials.delete(profile.secret_ref)
        self._app.profiles.delete(profile_id)
        log.info("Deleted profile %s", profile.name)
        self.refresh()
        self.profiles_changed.emit()

    def toggle_favorite(self, profile_id: str) -> None:
        profile = self._app.profiles.get(profile_id)
        profile.favorite = not profile.favorite
        profile.updated_at = _now_iso()
        self._app.profiles.upsert(profile)
        self.refresh()

    def refresh(self) -> None:
        query = self.search.text()
        profiles = self._app.profiles.search(query)
        self.list.clear()
        self.empty.setVisible(len(profiles) == 0)
        self.list.setVisible(len(profiles) > 0)
        for profile in profiles:
            item = QListWidgetItem()
            card = ServerCard(profile)
            item.setSizeHint(card.sizeHint().expandedTo(card.minimumSizeHint()))
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.list.addItem(item)
            self.list.setItemWidget(item, card)
            card.edit_requested.connect(self.edit_server)
            card.duplicate_requested.connect(self.duplicate_profile)
            card.delete_requested.connect(self.delete_profile)
            card.toggle_favorite_requested.connect(self.toggle_favorite)
            card.activated.connect(self._on_activated)
            card.ssh_requested.connect(self._on_activated)
            card.sftp_requested.connect(self._on_sftp)

    def _on_activated(self, profile_id: str) -> None:
        self._selected_id = profile_id
        self.ssh_requested.emit(profile_id)

    def _on_sftp(self, profile_id: str) -> None:
        self._selected_id = profile_id
        self.sftp_requested.emit(profile_id)

    def _on_reordered(self) -> None:
        ordered: list[str] = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is None:
                continue
            pid = item.data(Qt.ItemDataRole.UserRole)
            if pid:
                ordered.append(str(pid))
        if ordered:
            self._app.profiles.reorder(ordered)

    def _open_settings(self) -> None:
        window = self.window()
        open_settings = getattr(window, "open_settings", None)
        if callable(open_settings):
            open_settings()

    def _open_installer(self) -> None:
        from ghostport.ui.install_wizard import InstallWizard

        wizard = InstallWizard(self.window())
        wizard.exec()
