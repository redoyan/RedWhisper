# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


hidden_imports = (
    collect_submodules("faster_whisper")
    + collect_submodules("keyring.backends")
    + [
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "pystray._win32",
    ]
)
data_files = [
    ("assets/RedWhisper-icon.png", "assets"),
    ("assets/RedWhisper.ico", "assets"),
    ("assets/sound-levels-circle-transparent.png", "assets"),
]
data_files += collect_data_files("faster_whisper")
binaries = collect_dynamic_libs("ctranslate2")

a = Analysis(
    ["windows_app.py"],
    pathex=[],
    binaries=binaries,
    datas=data_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "AppKit",
        "Quartz",
        "rumps",
        "mlx",
        "mlx_lm",
        "mlx_whisper",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RedWhisper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/RedWhisper.ico",
    version="version_info.txt",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RedWhisper",
)
