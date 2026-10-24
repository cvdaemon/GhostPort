from __future__ import annotations

import shutil
import sys
from pathlib import Path

from ghostport.install.location import payload_source, source_root


_SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".venv",
    "venv",
    "dist",
    "build",
    "config",
    "logs",
    "tmp",
    "keys",
}


def copy_payload(dest: Path, *, progress=None) -> Path:
    """Copy the app into dest. Returns the path to the executable or launcher."""
    dest.mkdir(parents=True, exist_ok=True)
    src = payload_source()
    if src is None:
        if getattr(sys, "frozen", False):
            raise RuntimeError(
                "This Setup.exe does not include Ghostport. "
                "Rebuild with scripts\\build_windows.ps1"
            )
        _write_source_launcher(dest, progress=progress)
    elif src.resolve() != dest.resolve():
        _copy_tree(src, dest, progress=progress)
    flag = dest / "portable.flag"
    if not flag.is_file():
        flag.write_text("", encoding="utf-8")
    readme = dest / "README.txt"
    if not readme.is_file():
        readme.write_text(
            "Ghostport — no administrator install.\n"
            "Your servers and settings live in the config folder next to this app.\n"
            "Double-click Ghostport.exe (or Ghostport.cmd) to start.\n",
            encoding="utf-8",
        )
    exe = dest / "Ghostport.exe"
    if exe.is_file():
        return exe
    cmd = dest / "Ghostport.cmd"
    return cmd if cmd.is_file() else dest


def _copy_tree(src: Path, dest: Path, *, progress=None) -> None:
    files = [p for p in src.rglob("*") if p.is_file() and not _skip(p, src)]
    total = max(1, len(files))
    for i, path in enumerate(files, start=1):
        rel = path.relative_to(src)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
        if progress is not None:
            progress(i, total, str(rel))


def _skip(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in _SKIP_DIR_NAMES for part in rel.parts)


def _write_source_launcher(dest: Path, *, progress=None) -> None:
    python = Path(sys.executable).resolve()
    repo = source_root()
    # pythonw if we have it so no console flashes for noobs
    pythonw = python.with_name("pythonw.exe")
    runner = pythonw if pythonw.is_file() else python
    cmd = dest / "Ghostport.cmd"
    cmd.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        f'cd /d "{repo}"\r\n'
        f'start "" "{runner}" -m ghostport %*\r\n',
        encoding="utf-8",
    )
    if progress is not None:
        progress(1, 1, "Ghostport.cmd")


def launch_target(install_dir: Path) -> tuple[str, str, str, str]:
    """target, arguments, working_dir, icon."""
    exe = install_dir / "Ghostport.exe"
    if exe.is_file():
        icon = str(exe)
        return str(exe), "", str(install_dir), icon
    cmd = install_dir / "Ghostport.cmd"
    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    runner = pythonw if pythonw.is_file() else python
    repo = source_root()
    return str(runner), "-m ghostport", str(repo), str(runner)
