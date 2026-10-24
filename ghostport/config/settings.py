from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ghostport.config.paths import AppPaths

log = logging.getLogger("ghostport.config")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, indent=2) + "\n"
    tmp.write_text(data, encoding="utf-8")
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            bak.write_bytes(path.read_bytes())
        except OSError:
            log.warning("Could not write backup for %s", path)
    os_replace(tmp, path)


def os_replace(src: Path, dst: Path) -> None:
    import os

    os.replace(src, dst)


def quarantine_corrupt(path: Path) -> Path | None:
    if not path.is_file():
        return None
    dest = path.with_name(f"{path.name}.corrupt-{_utc_stamp()}")
    path.replace(dest)
    return dest


@dataclass
class AppearanceSettings:
    theme: str = "dark"
    dont_show_portable_secret_warning: bool = False


@dataclass
class TerminalSettings:
    font_family: str = ""
    font_size: int = 11
    cursor_style: str = "block"
    scrollback_lines: int = 4000
    copy_on_select: bool = True
    paste_warning_threshold: int = 2000
    term_type: str = "xterm-256color"


@dataclass
class ConnectionSettings:
    timeout_sec: int = 10
    keep_alive_interval_sec: int = 30
    reconnect_on_tab_focus: bool = False
    idle_timeout_min: int = 45


@dataclass
class SftpSettings:
    default_local_directory: str = ""
    show_hidden_files: bool = False
    confirm_deletes: bool = True
    confirm_overwrites: bool = True
    transfer_concurrency: int = 3
    local_column_widths: dict[str, int] = field(default_factory=dict)
    local_hidden_columns: list[str] = field(default_factory=list)
    remote_column_widths: dict[str, int] = field(default_factory=dict)
    remote_hidden_columns: list[str] = field(default_factory=list)
    editor_path: str = ""


@dataclass
class Settings:
    version: int = 1
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    terminal: TerminalSettings = field(default_factory=TerminalSettings)
    connections: ConnectionSettings = field(default_factory=ConnectionSettings)
    sftp: SftpSettings = field(default_factory=SftpSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        appearance = _dataclass_from_dict(AppearanceSettings, data.get("appearance") or {})
        terminal = _dataclass_from_dict(TerminalSettings, data.get("terminal") or {})
        connections = _dataclass_from_dict(ConnectionSettings, data.get("connections") or {})
        connections.idle_timeout_min = _clamp_int(connections.idle_timeout_min, 0, 24 * 60, 45)
        terminal.scrollback_lines = _clamp_int(terminal.scrollback_lines, 200, 10000, 4000)
        sftp = _dataclass_from_dict(SftpSettings, data.get("sftp") or {})
        sftp.local_column_widths = _int_dict(sftp.local_column_widths)
        sftp.remote_column_widths = _int_dict(sftp.remote_column_widths)
        sftp.local_hidden_columns = _str_list(sftp.local_hidden_columns)
        sftp.remote_hidden_columns = _str_list(sftp.remote_hidden_columns)
        version = int(data.get("version") or 1)
        return cls(
            version=version,
            appearance=appearance,
            terminal=terminal,
            connections=connections,
            sftp=sftp,
        )


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, parsed))


def _int_dict(data: Any) -> dict[str, int]:
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in data.items():
        try:
            width = int(value)
        except (TypeError, ValueError):
            continue
        if width > 0:
            out[str(key)] = width
    return out


def _str_list(data: Any) -> list[str]:
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str) and item]


def _dataclass_from_dict(cls: type, data: dict[str, Any]) -> Any:
    names = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in data.items() if k in names}
    return cls(**filtered)


class SettingsStore:
    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._settings = Settings()
        self.load_warning: str | None = None

    def load(self) -> Settings:
        self.load_warning = None
        path = self._paths.settings_file
        if not path.is_file():
            self._settings = Settings()
            return self._settings
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("settings.json root is not an object")
            self._settings = Settings.from_dict(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            dest = quarantine_corrupt(path)
            self.load_warning = (
                f"settings.json was corrupt and was moved to {dest.name if dest else 'backup'}."
            )
            log.exception("Failed to load settings from %s: %s", path, exc)
            self._settings = Settings()
        return self._settings

    def get(self) -> Settings:
        return self._settings

    def save(self, settings: Settings) -> None:
        self._settings = settings
        atomic_write_json(self._paths.settings_file, settings.to_dict())

    def update(self, **kwargs: Any) -> Settings:
        current = self._settings
        for key, value in kwargs.items():
            if not hasattr(current, key):
                raise AttributeError(f"Settings has no field {key!r}")
            setattr(current, key, value)
        self.save(current)
        return current
