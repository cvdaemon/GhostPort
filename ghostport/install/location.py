from __future__ import annotations

import os
import sys
from pathlib import Path


_FORBIDDEN_PREFIXES = (
    "program files",
    "program files (x86)",
    "windows",
    "programdata",
    "$recycle.bin",
    "system volume information",
)


def default_install_dir() -> Path:
    """Per-user folder. No admin. Not Program Files."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Ghostport"
    home = Path.home()
    return home / "AppData" / "Local" / "Ghostport"


def _windows_dir() -> Path | None:
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        return Path(windir)
    return Path("C:/Windows")


def is_drive_root(path: Path) -> bool:
    resolved = path.resolve()
    return resolved.parent == resolved


def is_forbidden_path(path: Path) -> bool:
    """True if installing here would need admin or write into Windows."""
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    if is_drive_root(resolved):
        return True
    parts = [p.lower() for p in resolved.parts]
    # C:\Program Files\..., C:\Windows\...
    if len(parts) >= 2 and parts[1] in _FORBIDDEN_PREFIXES:
        return True
    windir = _windows_dir()
    if windir is not None:
        try:
            resolved.relative_to(windir.resolve())
            return True
        except (ValueError, OSError):
            pass
    pf = os.environ.get("ProgramFiles")
    pf86 = os.environ.get("ProgramFiles(x86)")
    for env in (pf, pf86):
        if not env:
            continue
        try:
            resolved.relative_to(Path(env).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def can_write_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".ghostport-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def install_error(path: Path) -> str | None:
    """Human reason this folder is not OK, or None if it is."""
    if not str(path).strip():
        return "Choose a folder."
    if is_forbidden_path(path):
        return (
            "That folder needs an administrator password (Program Files, Windows, "
            "or a drive root). Pick a folder in your user profile — or another drive."
        )
    if not can_write_dir(path):
        return "Ghostport cannot write here without administrator permission. Choose another folder."
    return None


def payload_source() -> Path | None:
    """Frozen onedir or dist/Ghostport. None means run-from-source launcher."""
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        name = Path(sys.executable).name.lower()
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "Ghostport"
            if (bundled / "Ghostport.exe").is_file():
                return bundled
        sibling = here / "Ghostport"
        if (sibling / "Ghostport.exe").is_file():
            return sibling
        if "setup" not in name and (here / "Ghostport.exe").is_file():
            return here
        return None
    repo = Path(__file__).resolve().parents[2]
    dist = repo / "dist" / "Ghostport"
    if (dist / "Ghostport.exe").is_file() or (dist / "_internal").is_dir():
        return dist
    return None


def source_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
