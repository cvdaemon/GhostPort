from __future__ import annotations

from pathlib import Path

from ghostport.install.location import (
    default_install_dir,
    install_error,
    is_drive_root,
    is_forbidden_path,
)


def test_default_is_user_local_appdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    dest = default_install_dir()
    assert dest == tmp_path / "Local" / "Ghostport"
    assert "program files" not in str(dest).lower()


def test_program_files_forbidden(tmp_path: Path, monkeypatch) -> None:
    pf = tmp_path / "Program Files"
    pf.mkdir()
    monkeypatch.setenv("ProgramFiles", str(pf))
    target = pf / "Ghostport"
    assert is_forbidden_path(target) is True
    assert install_error(target) is not None


def test_windows_dir_forbidden(tmp_path: Path, monkeypatch) -> None:
    windir = tmp_path / "Windows"
    windir.mkdir()
    monkeypatch.setenv("WINDIR", str(windir))
    assert is_forbidden_path(windir / "System32") is True


def test_drive_root_forbidden() -> None:
    assert is_drive_root(Path("C:/")) is True
    assert is_forbidden_path(Path("C:/")) is True


def test_user_folder_allowed(tmp_path: Path) -> None:
    dest = tmp_path / "Ghostport"
    assert is_forbidden_path(dest) is False
    assert install_error(dest) is None
    assert dest.is_dir()


def test_setup_exe_does_not_treat_itself_as_payload(monkeypatch, tmp_path: Path) -> None:
    import ghostport.install.location as loc

    exe = tmp_path / "GhostportSetup.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(loc.sys, "frozen", True, raising=False)
    monkeypatch.setattr(loc.sys, "executable", str(exe))
    monkeypatch.setattr(loc.sys, "_MEIPASS", str(tmp_path / "missing"), raising=False)
    assert loc.payload_source() is None
