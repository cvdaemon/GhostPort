from __future__ import annotations

import subprocess
from pathlib import Path


def _ps_escape(value: str) -> str:
    return value.replace("'", "''")


def create_shortcut(
    link_path: Path,
    *,
    target: str,
    arguments: str = "",
    working_dir: str = "",
    icon: str = "",
    description: str = "Ghostport SSH client",
) -> None:
    """User-level .lnk via WScript.Shell. No admin, no HKLM."""
    link_path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$s = New-Object -ComObject WScript.Shell\n"
        f"$c = $s.CreateShortcut('{_ps_escape(str(link_path))}')\n"
        f"$c.TargetPath = '{_ps_escape(target)}'\n"
        f"$c.Arguments = '{_ps_escape(arguments)}'\n"
        f"$c.WorkingDirectory = '{_ps_escape(working_dir)}'\n"
        f"$c.Description = '{_ps_escape(description)}'\n"
    )
    if icon:
        script += f"$c.IconLocation = '{_ps_escape(icon)}'\n"
    script += "$c.Save()\n"
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "shortcut failed").strip()
        raise OSError(err)


def desktop_dir() -> Path:
    return Path.home() / "Desktop"


def start_menu_dir() -> Path:
    appdata = Path.home() / "AppData" / "Roaming"
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Ghostport"
