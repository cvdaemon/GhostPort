from __future__ import annotations

import posixpath
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QModelIndex, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ghostport.config.settings import SettingsStore
from ghostport.sftp.file_model import REMOTE_MIME, FileEntry, RemoteFileModel, entries_from_mime
from ghostport.ui.widgets.path_bar import PathBar

REMOTE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "Name"),
    ("size", "Size"),
    ("type", "Type"),
    ("modified", "Modified"),
    ("permissions", "Permissions"),
)
LOCAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "Name"),
    ("size", "Size"),
    ("type", "Type"),
    ("modified", "Modified"),
)
DEFAULT_COLUMN_WIDTHS: dict[str, int] = {
    "name": 280,
    "size": 90,
    "type": 100,
    "modified": 140,
    "permissions": 110,
}
_REQUIRED_COLUMN = "name"


class FilePane(QWidget):
    path_changed = Signal(str)
    activated_dir = Signal(str)
    activated_file = Signal(str)
    context_requested = Signal(object, object)
    local_paths_dropped = Signal(object)
    remote_entries_dropped = Signal(object)
    mkdir_requested = Signal()
    mkfile_requested = Signal()

    def __init__(
        self,
        title: str,
        *,
        remote: bool,
        parent: QWidget | None = None,
        settings_store: SettingsStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.remote = remote
        self._store = settings_store
        self._history: list[str] = []
        self._hist_index = -1
        self._show_hidden = False
        self._applying_columns = False
        self._columns = REMOTE_COLUMNS if remote else LOCAL_COLUMNS

        heading = QLabel(title)
        heading.setStyleSheet("font-weight: 600; color: #9d9d9d;")
        self.back_btn = QPushButton("←")
        self.fwd_btn = QPushButton("→")
        self.up_btn = QPushButton("↑")
        self.refresh_btn = QPushButton("↻")
        self.mkdir_btn = QPushButton("+ Folder")
        self.mkfile_btn = QPushButton("+ File")
        self.hidden_btn = QPushButton("Hidden")
        self.hidden_btn.setCheckable(True)
        for btn in (
            self.back_btn,
            self.fwd_btn,
            self.up_btn,
            self.refresh_btn,
            self.mkdir_btn,
            self.mkfile_btn,
            self.hidden_btn,
        ):
            btn.setFixedHeight(28)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.path_bar = PathBar()

        nav = QHBoxLayout()
        nav.setSpacing(4)
        nav.addWidget(self.back_btn)
        nav.addWidget(self.fwd_btn)
        nav.addWidget(self.up_btn)
        nav.addWidget(self.refresh_btn)
        nav.addWidget(self.mkdir_btn)
        nav.addWidget(self.mkfile_btn)
        nav.addWidget(self.hidden_btn)
        nav.addWidget(self.path_bar, 1)

        self.view = QTableView()
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.view.setSortingEnabled(True)
        self.view.verticalHeader().setVisible(False)
        self.view.setShowGrid(False)
        self.view.setAlternatingRowColors(True)
        self.view.setWordWrap(False)
        self.view.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.view.doubleClicked.connect(self._on_double)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._on_context)
        self.view.setDragEnabled(True)
        self.view.setAcceptDrops(True)
        self.view.setDropIndicatorShown(True)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.view.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.view.installEventFilter(self)
        self.view.viewport().installEventFilter(self)

        if remote:
            self.remote_model = RemoteFileModel(self)
            self.local_model = None
            self.view.setModel(self.remote_model)
        else:
            self.remote_model = None
            self.local_model = QFileSystemModel(self)
            self.local_model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs)
            self.local_model.setRootPath("")
            self.view.setModel(self.local_model)

        header = self.view.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(56)
        header.setDefaultSectionSize(100)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_menu)
        header.sectionResized.connect(self._on_section_resized)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._flush_columns)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(heading)
        layout.addLayout(nav)
        layout.addWidget(self.view, 1)

        self.back_btn.clicked.connect(self.go_back)
        self.fwd_btn.clicked.connect(self.go_forward)
        self.up_btn.clicked.connect(self.go_up)
        self.refresh_btn.clicked.connect(self.refresh)
        self.mkdir_btn.clicked.connect(self.mkdir_requested.emit)
        self.mkfile_btn.clicked.connect(self.mkfile_requested.emit)
        self.hidden_btn.toggled.connect(self._on_hidden)
        self.path_bar.submitted.connect(self._on_path_submit)
        self._apply_column_layout()

    def current_path(self) -> str:
        return self.path_bar.text().strip()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if watched in (self.view, self.view.viewport()) and event.type() in (
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        ):
            mime = event.mimeData()
            if self.remote and mime.hasUrls() and not mime.hasFormat(REMOTE_MIME):
                event.acceptProposedAction()
                if event.type() == QEvent.Type.Drop:
                    self._handle_drop(mime)
                return True
            if not self.remote and mime.hasFormat(REMOTE_MIME):
                event.acceptProposedAction()
                if event.type() == QEvent.Type.Drop:
                    self._handle_drop(mime)
                return True
        return super().eventFilter(watched, event)

    def _handle_drop(self, mime) -> None:
        if mime.hasFormat(REMOTE_MIME):
            entries = entries_from_mime(mime)
            if entries:
                self.remote_entries_dropped.emit(entries)
            return
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        paths = [p for p in paths if p]
        if paths:
            self.local_paths_dropped.emit(paths)

    def selected_local_paths(self) -> list[str]:
        if self.local_model is None:
            return []
        model = self.view.selectionModel()
        if model is None:
            return []
        paths: list[str] = []
        seen: set[str] = set()
        for index in model.selectedRows():
            path = self.local_model.filePath(index)
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
        return paths

    def selected_entries(self) -> list[FileEntry]:
        if self.remote_model is None:
            return []
        model = self.view.selectionModel()
        if model is None:
            return []
        entries: list[FileEntry] = []
        seen: set[str] = set()
        for index in model.selectedRows():
            entry = self.remote_model.entry_at(index.row())
            if entry is None or entry.path in seen:
                continue
            seen.add(entry.path)
            entries.append(entry)
        return entries

    def _on_context(self, pos) -> None:
        global_pos = self.view.viewport().mapToGlobal(pos)
        if not self.remote:
            menu = QMenu(self)
            folder = menu.addAction("New folder")
            new_file = menu.addAction("New file")
            chosen = menu.exec(global_pos)
            if chosen is folder:
                self.mkdir_requested.emit()
            elif chosen is new_file:
                self.mkfile_requested.emit()
            return
        entries = self.selected_entries()
        if not entries:
            index = self.view.indexAt(pos)
            if index.isValid() and self.remote_model is not None:
                entry = self.remote_model.entry_at(index.row())
                if entry is not None:
                    entries = [entry]
                    self.view.selectRow(index.row())
        self.context_requested.emit(entries, global_pos)

    def set_show_hidden(self, show: bool) -> None:
        self.hidden_btn.setChecked(show)
        self._apply_hidden(show)

    def _apply_hidden(self, show: bool) -> None:
        self._show_hidden = show
        if self.remote_model is not None:
            self.remote_model.set_show_hidden(show)
        if self.local_model is not None:
            flags = QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs
            if show:
                flags |= QDir.Filter.Hidden | QDir.Filter.System
            self.local_model.setFilter(flags)

    def _on_hidden(self, on: bool) -> None:
        self._apply_hidden(on)
        self.refresh()

    def navigate(self, path: str, *, record: bool = True) -> None:
        path = path.strip()
        if not path:
            return
        if record:
            self._history = self._history[: self._hist_index + 1]
            if not self._history or self._history[-1] != path:
                self._history.append(path)
                self._hist_index = len(self._history) - 1
        self.path_bar.setText(path)
        if self.local_model is not None:
            index = self.local_model.index(path)
            if index.isValid():
                self.view.setRootIndex(index)
        self.path_changed.emit(path)

    def set_remote_entries(self, path: str, entries: list[FileEntry]) -> None:
        if self.remote_model is None:
            return
        self.path_bar.setText(path)
        self.remote_model.set_entries(entries)
        if not self._history or self._history[self._hist_index] != path:
            self._history = self._history[: self._hist_index + 1]
            self._history.append(path)
            self._hist_index = len(self._history) - 1

    def go_back(self) -> None:
        if self._hist_index <= 0:
            return
        self._hist_index -= 1
        self.navigate(self._history[self._hist_index], record=False)
        if self.remote:
            self.path_changed.emit(self._history[self._hist_index])

    def go_forward(self) -> None:
        if self._hist_index + 1 >= len(self._history):
            return
        self._hist_index += 1
        self.navigate(self._history[self._hist_index], record=False)
        if self.remote:
            self.path_changed.emit(self._history[self._hist_index])

    def go_up(self) -> None:
        path = self.current_path()
        if self.remote:
            if path in ("/", ""):
                return
            parent = posixpath.dirname(path.rstrip("/")) or "/"
            self.navigate(parent)
            return
        current = Path(path)
        parent = current.parent
        if parent == current:
            self.navigate("")
            if self.local_model is not None:
                self.view.setRootIndex(self.local_model.index(""))
            return
        self.navigate(str(parent))

    def refresh(self) -> None:
        path = self.current_path()
        if self.remote:
            self.path_changed.emit(path)
            return
        if self.local_model is not None:
            self.local_model.setRootPath(self.local_model.rootPath())
            if path:
                self.view.setRootIndex(self.local_model.index(path))

    def _on_path_submit(self, path: str) -> None:
        self.navigate(path)

    def _on_double(self, index: QModelIndex) -> None:
        if self.remote_model is not None:
            entry = self.remote_model.entry_at(index.row())
            if entry is None:
                return
            if entry.is_dir:
                self.activated_dir.emit(entry.path)
            else:
                self.activated_file.emit(entry.path)
            return
        if self.local_model is None:
            return
        path = self.local_model.filePath(index)
        info_dir = self.local_model.isDir(index)
        if info_dir:
            self.navigate(path)
        else:
            self.activated_file.emit(path)

    def column_key(self, index: int) -> str | None:
        if 0 <= index < len(self._columns):
            return self._columns[index][0]
        return None

    def set_column_visible(self, key: str, visible: bool) -> None:
        if key == _REQUIRED_COLUMN:
            return
        index = self._index_for_key(key)
        if index is None:
            return
        header = self.view.horizontalHeader()
        header.setSectionHidden(index, not visible)
        self._remember_hidden()
        self._flush_columns()

    def _index_for_key(self, key: str) -> int | None:
        for i, (col_key, _label) in enumerate(self._columns):
            if col_key == key:
                if i < self.view.horizontalHeader().count():
                    return i
                return None
        return None

    def _apply_column_layout(self) -> None:
        header = self.view.horizontalHeader()
        count = header.count()
        if count <= 0:
            return
        widths, hidden = self._prefs()
        self._applying_columns = True
        try:
            header.setStretchLastSection(False)
            for i in range(count):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                key = self.column_key(i)
                if key is None:
                    continue
                width = widths.get(key, DEFAULT_COLUMN_WIDTHS.get(key, 100))
                header.resizeSection(i, max(header.minimumSectionSize(), int(width)))
                header.setSectionHidden(i, key != _REQUIRED_COLUMN and key in hidden)
        finally:
            self._applying_columns = False

    def _on_header_menu(self, pos) -> None:
        header = self.view.horizontalHeader()
        menu = QMenu(self)
        actions: list[tuple[object, str]] = []
        for i, (key, fallback) in enumerate(self._columns):
            if i >= header.count():
                break
            model = self.view.model()
            label = fallback
            if model is not None:
                text = model.headerData(i, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
                if isinstance(text, str) and text.strip():
                    label = text
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(i))
            action.setEnabled(key != _REQUIRED_COLUMN)
            actions.append((action, key))
        chosen = menu.exec(header.mapToGlobal(pos))
        for action, key in actions:
            if chosen is action:
                self.set_column_visible(key, action.isChecked())
                return

    def _on_section_resized(self, index: int, _old: int, new: int) -> None:
        if self._applying_columns or new <= 0:
            return
        key = self.column_key(index)
        if key is None:
            return
        self._remember_width(key, new)
        self._save_timer.start()

    def _prefs(self) -> tuple[dict[str, int], list[str]]:
        if self._store is None:
            return {}, []
        sftp = self._store.get().sftp
        raw_widths = sftp.remote_column_widths if self.remote else sftp.local_column_widths
        raw_hidden = sftp.remote_hidden_columns if self.remote else sftp.local_hidden_columns
        widths: dict[str, int] = {}
        if isinstance(raw_widths, dict):
            for key, value in raw_widths.items():
                try:
                    width = int(value)
                except (TypeError, ValueError):
                    continue
                if width > 0:
                    widths[str(key)] = width
        hidden = [str(item) for item in raw_hidden] if isinstance(raw_hidden, list) else []
        return widths, hidden

    def _width_target(self) -> dict[str, int]:
        if self._store is None:
            return {}
        sftp = self._store.get().sftp
        target = sftp.remote_column_widths if self.remote else sftp.local_column_widths
        if not isinstance(target, dict):
            target = {}
            if self.remote:
                sftp.remote_column_widths = target
            else:
                sftp.local_column_widths = target
        return target

    def _remember_width(self, key: str, width: int) -> None:
        if self._store is None:
            return
        self._width_target()[key] = int(width)

    def _remember_hidden(self) -> None:
        if self._store is None:
            return
        header = self.view.horizontalHeader()
        hidden = [
            key
            for i, (key, _label) in enumerate(self._columns)
            if i < header.count() and key != _REQUIRED_COLUMN and header.isSectionHidden(i)
        ]
        sftp = self._store.get().sftp
        if self.remote:
            sftp.remote_hidden_columns = hidden
        else:
            sftp.local_hidden_columns = hidden

    def _flush_columns(self) -> None:
        if self._store is None:
            return
        self._save_timer.stop()
        self._store.save(self._store.get())

    def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._save_timer.isActive():
            self._flush_columns()
        super().hideEvent(event)
