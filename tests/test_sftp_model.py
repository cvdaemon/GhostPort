from __future__ import annotations

import stat

from paramiko.sftp_attr import SFTPAttributes

from ghostport.sftp.file_model import RemoteFileModel, entry_from_attr


def _attr(name: str, *, directory: bool, size: int = 0) -> SFTPAttributes:
    attr = SFTPAttributes()
    attr.filename = name
    attr.st_size = size
    attr.st_mtime = 1_700_000_000
    attr.st_mode = (stat.S_IFDIR | 0o755) if directory else (stat.S_IFREG | 0o644)
    return attr


def test_skips_dot_entries() -> None:
    assert entry_from_attr("/home", _attr(".", directory=True)) is None
    assert entry_from_attr("/home", _attr("..", directory=True)) is None


def test_paths_join() -> None:
    file_e = entry_from_attr("/home/simtra", _attr("notes.txt", directory=False, size=12))
    assert file_e is not None
    assert file_e.path == "/home/simtra/notes.txt"
    assert file_e.is_dir is False
    root = entry_from_attr("/", _attr("etc", directory=True))
    assert root is not None
    assert root.path == "/etc"
    assert root.is_dir is True


def test_hidden_filter() -> None:
    model = RemoteFileModel()
    visible = entry_from_attr("/h", _attr("visible", directory=False))
    hidden = entry_from_attr("/h", _attr(".secret", directory=False))
    assert visible and hidden
    model.set_entries([visible, hidden])
    assert model.rowCount() == 1
    model.set_show_hidden(True)
    assert model.rowCount() == 2
