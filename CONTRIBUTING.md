# Contributing

## Setup

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ghostport
```

Run commands from the repository root.

## Conventions

- Python 3.10, PySide6, Paramiko, pyte
- GUI thread owns widgets; Paramiko lives on native `QThread`s
- Secrets never go in JSON; use `CredentialStore`
- Keep the terminal history a rolling window (`PtyBridge` + `clip_pending_bytes`)

## Release

1. Bump `ghostport/__init__.py` (`__version__`) and `CHANGELOG.md`
2. `powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1`
3. Ship `dist/GhostportSetup/GhostportSetup.exe`
