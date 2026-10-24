<p align="center">
  <img src="ghostport/resources/icons/app.svg" width="72" height="72" alt="Ghostport">
</p>

<h1 align="center">Ghostport</h1>

<p align="center">
  Portable Windows SSH + SFTP client.<br>
  One click to your servers. No administrator account.
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3d9b94">
  <img alt="Windows" src="https://img.shields.io/badge/windows-portable-1e1e1e">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-6b6b6b">
  <img alt="Version" src="https://img.shields.io/badge/version-3.1.0-3d9b94">
</p>

Ghostport is a user-space replacement for Xshell + Xftp: saved server profiles, a dark tabbed terminal, and a linked two-pane SFTP window that reuses the same credentials.

## Features

- **SSH first** — favorites on the home screen, one click to connect
- **SFTP** — separate window, same profile, no second login; opens at the shell’s current directory
- **Portable** — runs from a folder; optional per-user installer, never Program Files
- **Secrets** — DPAPI blobs next to the app (portable) or Windows Credential Locker (installed)
- **Transfers** — progress, speed, ETA, pause/resume; remote Edit uploads on save
- **Splits** — drag a tab to an edge; up to six panes
- **Idle close** — SSH/SFTP tabs close after 45 minutes with no activity (configurable)
- **Bounded terminal** — rolling scrollback so `docker logs` cannot fill RAM

## Run it

Clone this repo. From the **repository root** (the folder that contains `pyproject.toml`, not the inner `ghostport` package):

```powershell
python -m pip install -r requirements.txt
python -m ghostport
```

Python 3.10+ on Windows. `python -m ghostport --setup` opens the per-user installer wizard.

## Build a Windows exe

The GitHub tree is source only. After a local build, PyInstaller writes packages under `dist/` on your machine (that folder is gitignored):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

| Local path | What it is |
| --- | --- |
| `dist/Ghostport/Ghostport.exe` | Portable app (keep `_internal` and `portable.flag` beside it) |
| `dist/GhostportSetup/GhostportSetup.exe` | Per-user installer, no admin |

Installer flow: double-click the setup exe → Next → pick a user folder or another drive → Install → Finish. Then open Ghostport from the Desktop or Start menu shortcut.

## Development

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

Python 3.10+, Windows. Stack: PySide6, Paramiko, pyte.

## Layout

```
ghostport/          application package
tests/              pytest
scripts/            Windows icon + PyInstaller build
docs/               architecture, backlog, original brief
ghostport.spec      app bundle
ghostport-setup.spec  installer bundle
```

## Docs

- [Changelog](CHANGELOG.md)
- [Architecture](docs/architecture.md)
- [Backlog](docs/backlog.md)

## License

[MIT](LICENSE)
