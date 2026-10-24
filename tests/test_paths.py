from __future__ import annotations

from pathlib import Path

import pytest

from ghostport.config.paths import AppPaths


def test_portable_flag_present(tmp_path: Path) -> None:
    (tmp_path / "portable.flag").write_text("", encoding="utf-8")
    paths = AppPaths.resolve(tmp_path)
    assert paths.is_portable is True
    assert paths.root == tmp_path.resolve()
    assert paths.profiles_file == tmp_path.resolve() / "config" / "profiles.json"
    assert paths.temp_dir == tmp_path.resolve() / "tmp"


def test_absent_flag_and_no_config_uses_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "AppData"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    empty = tmp_path / "install"
    empty.mkdir()
    paths = AppPaths.resolve(empty)
    assert paths.is_portable is False
    assert paths.root == appdata / "Ghostport"


def test_empty_config_dir_does_not_force_portable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "AppData"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    install = tmp_path / "install"
    install.mkdir()
    (install / "config").mkdir()
    paths = AppPaths.resolve(install)
    assert paths.is_portable is False


def test_leftover_profiles_json_without_flag_is_portable(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "profiles.json").write_text("{}", encoding="utf-8")
    paths = AppPaths.resolve(tmp_path)
    assert paths.is_portable is True
    assert paths.root == tmp_path.resolve()


def test_ensure_dirs_after_resolve(tmp_path: Path) -> None:
    (tmp_path / "portable.flag").write_text("", encoding="utf-8")
    paths = AppPaths.resolve(tmp_path)
    assert not paths.config_dir.exists()
    paths.ensure_dirs()
    assert paths.config_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.keys_dir.is_dir()
    assert paths.secrets_dir.is_dir()
    assert paths.temp_dir.is_dir()


def test_temp_dir_portable_is_root_tmp(tmp_path: Path) -> None:
    (tmp_path / "portable.flag").write_text("", encoding="utf-8")
    paths = AppPaths.resolve(tmp_path)
    assert paths.temp_dir == tmp_path.resolve() / "tmp"
