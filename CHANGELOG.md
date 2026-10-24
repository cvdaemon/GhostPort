# Changelog

All notable changes to Ghostport are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.1.0] — 2026-10-14

### Fixed

- Tailing high-volume output (`docker logs`, busy journals) could freeze the app as every line stayed in RAM. The terminal now keeps a rolling window (default 4000 lines, max 10 000). Floods of pending bytes keep only the latest chunk so the UI stays on the end of the log.

### Changed

- Repository layout matches a typical GitHub project (`docs/`, `LICENSE`, `pyproject.toml`).

## [3.0.0] — 2026-10-13

Version 3. Same Ghostport icon is now on `Ghostport.exe` and `GhostportSetup.exe` in Explorer, the taskbar, and shortcuts.

### Changed

- Version number is **3.0.0**.
- Packaged Windows executables use `app.svg` (as `app.ico`) instead of the default PyInstaller icon.

## [1.0.1] — 2026-10-13

### Fixed

- Text looked blurry on laptops with Windows display scaling (125% / 150% / 175%). The packaged exe now declares per-monitor DPI awareness, and the terminal paints on a full-resolution canvas instead of stretching a 96-dpi bitmap.

## [1.0.0] — 2026-10-09

First daily-driver release after a month of real use. SSH and SFTP are both in the same portable app.

### Added

- Idle timeout: SSH and SFTP tabs close themselves after **45 minutes** with no keyboard, mouse, or transfer activity. No dialog. Change the duration (or set Never) under Settings → Connections.
- SFTP **New folder** and **New file** (toolbar and remote right-click).
- Open SFTP from an SSH tab at the shell’s current directory (prompt path, including `~`).
- Transfer queue: progress, size, speed, and ETA; **Show past** toggle (hidden by default); pause / resume / cancel.
- Remote **Edit**: pick an editor the first time, keep the file in temp, upload again when you save.
- SFTP column menu (right-click the header to show or hide Name, Size, Type, Modified, Permissions). Column widths persist.
- Drag-select in the SSH terminal across the full scrollback, including auto-scroll at the edges.

### Changed

- The SFTP window stays open when the SSH window is closed.
- The Name column is drag-resizable so long file names stay visible.
- Transfer ETA uses the last 100 progress samples instead of the last tick. Speed is shown as MB/s so the column does not jump width.
- One Transfers tab per SFTP window (drag-and-drop no longer opens a new tab per file).
- The Transfers tab hides itself shortly after the queue is idle.
- Terminal palette on the dark theme: prompt paths and other ANSI blues stay readable.
- Bold ANSI colors map to the bright set.

### Fixed

- Dragging a remote file to the local pane opened many Transfers tabs.
- Drag-copy in SSH could not cover large scrolled regions.
- `~/…` SFTP paths were treated as relative instead of home.
- Split / resize of SSH panes could leave the terminal blank.
- `python -m ghostport` must be run from the project root (`E:\abi\Ghostport`), not from the inner `ghostport` package folder.

## [0.1.0] — 2026-09-16

First working portable build.

### Added

- Portable Windows SSH + SFTP client (Python 3.10, PySide6, Paramiko, pyte). No admin, no Program Files.
- Saved server profiles, favorites, search, password / private-key / agent auth.
- Secrets in DPAPI blobs (portable) or Windows Credential Locker (installed).
- TOFU host-key verification.
- Multi-tab SSH with a cell-grid terminal (pyte), split panes, dark theme.
- Separate two-pane SFTP window using the same profile credentials (no second login).
- PyInstaller onedir app plus a user-folder installer (`GhostportSetup.exe`).
