"""Standalone Ghostport Setup.exe entry. No SSH stack, no admin."""

from __future__ import annotations

from ghostport.ui.install_wizard import run_wizard

if __name__ == "__main__":
    raise SystemExit(run_wizard())
