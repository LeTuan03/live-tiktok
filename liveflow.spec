# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for LiveFlow AI Studio.
# Build:  BUILD_EXE.bat   (or: python -m PyInstaller --noconfirm --clean liveflow.spec)

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

hiddenimports = (
    collect_submodules("keyring.backends")
    + collect_submodules("obsws_python")
    + collect_submodules("edge_tts")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + [
        "PySide6.QtMultimedia",
        "pydantic_settings",
        "aiohttp",
        "bs4",
        "win32ctypes.pywin32",
    ]
)

datas = collect_data_files("edge_tts") + collect_data_files("certifi")

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=[],
    datas=datas + [(".env.example", ".")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "scipy", "PyQt5", "PyQt6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiveFlowAIStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LiveFlowAIStudio",
)
