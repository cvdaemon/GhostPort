from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, Qt

from paramiko.sftp_attr import SFTPAttributes


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified_ts: float | None
    mode: int | None
    is_link: bool = False

    def type_label(self) -> str:
        if self.is_link:
            return "Link"
        if self.is_dir:
            return "Folder"
        return "File"

    def size_label(self) -> str:
        if self.is_dir:
            return ""
        n = self.size
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f" {n / 1024:.1f} KB".strip()
        if n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.1f} MB"
        return f"{n / (1024 * 1024 * 1024):.1f} GB"

    def modified_label(self) -> str:
        if self.modified_ts is None:
            return ""
        return datetime.fromtimestamp(self.modified_ts).strftime("%Y-%m-%d %H:%M")

    def mode_label(self) -> str:
        if self.mode is None:
            return ""
        return stat.filemode(self.mode)


def entry_from_attr(cwd: str, attr: SFTPAttributes) -> FileEntry | None:
    name = attr.filename or ""
    if name in (".", "..") or not name:
        return None
    mode = int(attr.st_mode) if attr.st_mode is not None else None
    is_dir = bool(mode is not None and stat.S_ISDIR(mode))
    is_link = bool(mode is not None and stat.S_ISLNK(mode))
    if cwd == "/":
        path = "/" + name
    elif cwd == ".":
        path = name
    else:
        path = cwd.rstrip("/") + "/" + name
    return FileEntry(
        name=name,
        path=path,
        is_dir=is_dir,
        size=int(attr.st_size or 0),
        modified_ts=float(attr.st_mtime) if attr.st_mtime is not None else None,
        mode=mode,
        is_link=is_link,
    )


_HEADERS = ("Name", "Size", "Type", "Modified", "Permissions")
REMOTE_MIME = "application/x-ghostport-remote"


def entries_to_mime(entries: list[FileEntry]) -> QMimeData:
    payload = [
        {
            "name": e.name,
            "path": e.path,
            "is_dir": e.is_dir,
            "size": e.size,
            "modified_ts": e.modified_ts,
            "mode": e.mode,
            "is_link": e.is_link,
        }
        for e in entries
    ]
    data = QMimeData()
    data.setData(REMOTE_MIME, json.dumps(payload).encode("utf-8"))
    return data


def entries_from_mime(data: QMimeData) -> list[FileEntry]:
    if not data.hasFormat(REMOTE_MIME):
        return []
    raw = bytes(data.data(REMOTE_MIME)).decode("utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[FileEntry] = []
    if not isinstance(payload, list):
        return []
    for item in payload:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        out.append(
            FileEntry(
                name=str(item.get("name") or ""),
                path=str(item["path"]),
                is_dir=bool(item.get("is_dir")),
                size=int(item.get("size") or 0),
                modified_ts=float(item["modified_ts"]) if item.get("modified_ts") is not None else None,
                mode=int(item["mode"]) if item.get("mode") is not None else None,
                is_link=bool(item.get("is_link")),
            )
        )
    return out


class RemoteFileModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all: list[FileEntry] = []
        self._rows: list[FileEntry] = []
        self._show_hidden = False
        self._sort_col = 0
        self._sort_asc = True

    def set_show_hidden(self, show: bool) -> None:
        self._show_hidden = show
        self._rebuild()

    def set_entries(self, entries: list[FileEntry]) -> None:
        self.beginResetModel()
        self._all = list(entries)
        self._apply_filter_sort()
        self.endResetModel()

    def entry_at(self, row: int) -> FileEntry | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        return 5

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = 0):  # type: ignore[override]
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(_HEADERS):
                return _HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = 0):  # type: ignore[override]
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        entry = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            col = index.column()
            if col == 0:
                prefix = "📁 " if entry.is_dir else "📄 "
                return prefix + entry.name
            if col == 1:
                return entry.size_label()
            if col == 2:
                return entry.type_label()
            if col == 3:
                return entry.modified_label()
            if col == 4:
                return entry.mode_label()
        if role == Qt.ItemDataRole.UserRole:
            return entry
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # type: ignore[override]
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
        )

    def mimeTypes(self) -> list[str]:  # type: ignore[override]
        return [REMOTE_MIME]

    def mimeData(self, indexes) -> QMimeData:  # type: ignore[override]
        rows = sorted({index.row() for index in indexes if index.isValid()})
        entries = [e for e in (self.entry_at(r) for r in rows) if e is not None]
        return entries_to_mime(entries)

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:  # type: ignore[override]
        self._sort_col = column
        self._sort_asc = order == Qt.SortOrder.AscendingOrder
        self._rebuild()

    def _rebuild(self) -> None:
        self.beginResetModel()
        self._apply_filter_sort()
        self.endResetModel()

    def _apply_filter_sort(self) -> None:
        rows = [e for e in self._all if self._show_hidden or not e.name.startswith(".")]
        col = self._sort_col

        def key(entry: FileEntry):
            if col == 1:
                return (not entry.is_dir, entry.size, entry.name.lower())
            if col == 2:
                return (not entry.is_dir, entry.type_label(), entry.name.lower())
            if col == 3:
                return (not entry.is_dir, entry.modified_ts or 0, entry.name.lower())
            if col == 4:
                return (not entry.is_dir, entry.mode_label(), entry.name.lower())
            return (not entry.is_dir, entry.name.lower())

        rows.sort(key=key, reverse=not self._sort_asc)
        self._rows = rows
