"""Filesystem locations used by MeetSub.

Everything is deliberately kept in per-user directories.  A portable checkout
or a frozen build can ship pre-downloaded models in ``<app>/models``; those are
used in place, while downloaded models fall back to LocalAppData.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "MeetSub"
DEFAULT_MODEL_ROOT_NAME = "models"


def runtime_root() -> Path:
    """Directory containing the application (source checkout or frozen build)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _roaming_base() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _local_base() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def data_dir() -> Path:
    override = os.environ.get("MEETSUB_DATA_DIR")
    path = Path(override) if override else _roaming_base() / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_data_dir() -> Path:
    path = _local_base() / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = local_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = local_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    override = os.environ.get("MEETSUB_CONFIG")
    return Path(override) if override else data_dir() / "config.json"


def secrets_path() -> Path:
    return data_dir() / "secrets.bin"


def known_documents_dir() -> Path:
    """Best-effort localized Documents folder on Windows."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            folder_id = GUID(
                0xFDD39AD0,
                0x238F,
                0x46AF,
                (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
            )
            out = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            ole32 = ctypes.windll.ole32
            shell32.SHGetKnownFolderPath.argtypes = [
                ctypes.POINTER(GUID),
                wintypes.DWORD,
                wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_wchar_p),
            ]
            shell32.SHGetKnownFolderPath.restype = ctypes.c_long
            result = shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out))
            if result == 0 and out.value:
                path = Path(out.value)
                ole32.CoTaskMemFree(out)
                return path
        except Exception:
            pass
    return Path.home() / "Documents"


def default_transcript_dir() -> Path:
    return known_documents_dir() / APP_DIR_NAME / "transcripts"


def model_roots() -> list[Path]:
    """Locations searched for pre-downloaded models, in priority order."""
    roots: list[Path] = []
    env_root = os.environ.get("MEETSUB_MODEL_DIR")
    if env_root:
        roots.append(Path(env_root))
    roots.append(runtime_root() / DEFAULT_MODEL_ROOT_NAME)
    roots.append(local_data_dir() / DEFAULT_MODEL_ROOT_NAME)
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def writable_model_root() -> Path:
    """First model root that can actually hold a new file."""
    for root in model_roots():
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return root
        except OSError:
            continue
    fallback = local_data_dir() / DEFAULT_MODEL_ROOT_NAME
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def find_dir_with_files(name: str, required: list[str], min_sizes: dict[str, int] | None = None) -> Path | None:
    """Return an existing model directory that satisfies basic completeness checks."""
    min_sizes = min_sizes or {}
    for root in model_roots():
        candidate = root / name
        if not candidate.is_dir():
            continue
        ok = True
        for rel in required:
            path = candidate / rel
            if not path.is_file():
                ok = False
                break
            minimum = min_sizes.get(rel)
            if minimum is not None and path.stat().st_size < minimum:
                ok = False
                break
        if ok:
            return candidate
    return None
