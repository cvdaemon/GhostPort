# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["ghostport/main.py"],
    pathex=["."],
    binaries=[],
    datas=[("ghostport/resources", "ghostport/resources")],
    hiddenimports=[
        "PySide6.QtSvg",
        "paramiko.win_pageant",
        "paramiko.win_openssh",
        "keyring.backends.Windows",
    ],
    excludes=["pytest", "numpy", "PIL", "Pillow", "pygments", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ghostport",
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
    name="Ghostport",
)
