"""Credential vault: API keys and passwords never go into the database or the UI.

Uses the OS credential store through `keyring` (Windows Credential Manager, macOS Keychain,
Secret Service on Linux). When no keyring backend is available, falls back to a file in the
data folder that is encrypted with Windows DPAPI where possible and readable only by the user.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
from typing import Any

from . import config

SERVICE = "ai-lifeos"
_lock = threading.Lock()


def _keyring() -> Any | None:
    if os.environ.get("LIFEOS_NO_KEYRING"):
        return None
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def backend() -> str:
    kr = _keyring()
    if kr is not None:
        return f"os-keyring ({type(kr.get_keyring()).__name__})"
    return "dpapi-file" if sys.platform.startswith("win") else "file (owner-only permissions)"


# ---------------------------------------------------------------- file fallback

def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _file() -> Any:
    return config.data_dir() / "secrets.bin"


def _load_file() -> dict[str, str]:
    path = _file()
    if not path.exists():
        return {}
    raw = path.read_bytes()
    if sys.platform.startswith("win"):
        raw = _dpapi(raw, encrypt=False)
    else:
        raw = base64.b64decode(raw)
    return json.loads(raw.decode())


def _save_file(data: dict[str, str]) -> None:
    raw = json.dumps(data).encode()
    raw = _dpapi(raw, encrypt=True) if sys.platform.startswith("win") else base64.b64encode(raw)
    path = _file()
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(raw)
    if not sys.platform.startswith("win"):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------- API

def set_secret(name: str, value: str) -> None:
    kr = _keyring()
    with _lock:
        if kr is not None:
            kr.set_password(SERVICE, name, value)
            return
        data = _load_file()
        data[name] = value
        _save_file(data)


def get_secret(name: str) -> str | None:
    kr = _keyring()
    with _lock:
        if kr is not None:
            try:
                return kr.get_password(SERVICE, name)
            except Exception:
                return None
        return _load_file().get(name)


def delete_secret(name: str) -> None:
    kr = _keyring()
    with _lock:
        if kr is not None:
            try:
                kr.delete_password(SERVICE, name)
            except Exception:
                pass
            return
        data = _load_file()
        if data.pop(name, None) is not None:
            _save_file(data)


def has_secret(name: str) -> bool:
    return bool(get_secret(name))
