from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path
    config_dir: Path
    logs_dir: Path
    keys_dir: Path
    secrets_dir: Path
    profiles_file: Path
    settings_file: Path
    known_hosts_file: Path
    temp_dir: Path
    is_portable: bool

    @staticmethod
    def resolve(exe_or_source_dir: Path | None = None) -> AppPaths:
        if exe_or_source_dir is not None:
            install_dir = Path(exe_or_source_dir).resolve()
        elif getattr(sys, "frozen", False):
            install_dir = Path(sys.executable).resolve().parent
        else:
            install_dir = Path(__file__).resolve().parents[2]

        flag = install_dir / "portable.flag"
        config_dir = install_dir / "config"
        has_user_config = config_dir.is_dir() and (
            (config_dir / "profiles.json").is_file()
            or (config_dir / "settings.json").is_file()
        )
        is_portable = flag.is_file() or has_user_config
        if is_portable:
            root = install_dir
            temp_dir = root / "tmp"
        else:
            appdata = os.environ.get("APPDATA")
            if not appdata:
                raise RuntimeError("APPDATA is not set; cannot resolve AppData path")
            root = Path(appdata) / "Ghostport"
            temp = os.environ.get("TEMP") or os.environ.get("TMP") or str(root / "tmp")
            temp_dir = Path(temp) / "Ghostport"

        config_dir = root / "config"
        return AppPaths(
            root=root,
            config_dir=config_dir,
            logs_dir=root / "logs",
            keys_dir=root / "keys",
            secrets_dir=config_dir / "secrets",
            profiles_file=config_dir / "profiles.json",
            settings_file=config_dir / "settings.json",
            known_hosts_file=config_dir / "known_hosts",
            temp_dir=temp_dir,
            is_portable=is_portable,
        )

    def ensure_dirs(self) -> None:
        for path in (
            self.config_dir,
            self.logs_dir,
            self.keys_dir,
            self.secrets_dir,
            self.temp_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
