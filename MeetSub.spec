# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("faster_whisper") + collect_data_files("opencc")

hiddenimports = [
    "ctranslate2",
    "tokenizers",
    "sentencepiece",
    "opencc",
    "onnxruntime",
    "av",
    "pyaudiowpatch",
    "webrtcvad",
    "soxr",
    "faster_whisper",
    "huggingface_hub",
    "httpx",
    "httpcore",
    "PySide6.QtNetwork",
]

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["packaging-hooks"],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pandas",
        "scipy",
        "torch",
        "transformers",
        "argostranslate",
        "pytest",
        "_pytest",
        "pluggy",
        "iniconfig",
        "PIL",
        "Pillow",
        "pygments",
        "setuptools",
        "pkg_resources",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtQml",
        "PySide6.QtQuick",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetSub",
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
    icon=["assets/meetsub.ico"],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MeetSub",
)
