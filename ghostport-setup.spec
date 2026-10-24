# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

datas = [("ghostport/resources", "ghostport/resources")]
payload = Path("dist/Ghostport")
if payload.is_dir():
    datas.append((str(payload), "Ghostport"))

a = Analysis(
    ["ghostport/setup_main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=["PySide6.QtSvg"],
    excludes=[
        "paramiko",
        "pyte",
        "cryptography",
        "nacl",
        "bcrypt",
        "keyring",
        "pytest",
        "numpy",
        "PIL",
        "Pillow",
        "pygments",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GhostportSetup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=False,
    disable_windowed_traceback=False,
    icon="ghostport/resources/icons/app.ico",
    manifest="ghostport/resources/windows/app.manifest",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="GhostportSetup",
)
