"""Windows DPAPI helpers for at-rest protection of the optional API key."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class SecretProtectionError(RuntimeError):
    pass


if os.name == "nt":

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL


def _blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def protect(data: bytes, description: str = "MeetSub secret") -> bytes:
    if os.name != "nt":
        raise SecretProtectionError("Windows DPAPI is unavailable on this platform.")
    input_blob = _blob(data)
    output_blob = _DataBlob()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        description,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise SecretProtectionError("Windows DPAPI could not encrypt the secret.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        _kernel32.LocalFree(output_blob.pbData)


def unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SecretProtectionError("Windows DPAPI is unavailable on this platform.")
    input_blob = _blob(data)
    output_blob = _DataBlob()
    description = wintypes.LPWSTR()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        ctypes.byref(description),
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise SecretProtectionError("Windows DPAPI could not decrypt the secret.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if description:
            _kernel32.LocalFree(description)
        _kernel32.LocalFree(output_blob.pbData)
