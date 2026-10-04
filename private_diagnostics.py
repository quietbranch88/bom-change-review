"""Local Windows-user encrypted error evidence; never auto-print private content."""

import argparse
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re

MAX_BODY_BYTES = 8192
MAX_ARCHIVE_BYTES = 32768
FORMAT = "windows-dpapi-current-user-v1"


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, *, decrypt=False):
    if os.name != "nt":
        raise OSError("private_archive_requires_windows")
    crypt = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    try:
        # UI_FORBIDDEN=1. Do not set LOCAL_MACHINE=4: protection is user-scoped.
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise OSError("private_archive_crypto_failed")
        if target.size > MAX_ARCHIVE_BYTES:
            raise ValueError("private_archive_crypto_size")
        return ctypes.string_at(target.data, target.size)
    finally:
        if target.data:
            ctypes.memset(target.data, 0, target.size)
            kernel.LocalFree(target.data)
        ctypes.memset(buffer, 0, ctypes.sizeof(buffer))


def seal(raw, key=None):
    if not isinstance(raw, bytes) or len(raw) > MAX_BODY_BYTES:
        raise ValueError("private_archive_body_size")
    if key:
        raw = raw.replace(key.encode("utf-8"), b"[REDACTED]")
    raw = re.sub(rb"sk-or-[A-Za-z0-9_-]+", b"[REDACTED]", raw)
    raw = re.sub(rb"(?i)(?:authorization\s*[:=]|bearer\s+)\s*[^\r\n]*", b"[REDACTED]", raw)
    # Other personal/vendor text is deliberately recoverable, but only encrypted.
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("private_archive_redacted_size")
    encrypted = _dpapi(raw)
    return {"format": FORMAT, "ciphertext": base64.b64encode(encrypted).decode("ascii")}


def unseal(archive):
    if not isinstance(archive, dict) or set(archive) != {"format", "ciphertext"} or archive["format"] != FORMAT:
        raise ValueError("private_archive_format")
    encoded = archive["ciphertext"]
    if not isinstance(encoded, str) or len(encoded) > MAX_ARCHIVE_BYTES:
        raise ValueError("private_archive_size")
    data = base64.b64decode(encoded, validate=True)
    raw = _dpapi(data, decrypt=True)
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("private_archive_body_size")
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--reveal-private", action="store_true",
                        help="Explicitly reveal PRIVATE error bytes as escaped text in this local terminal; do not share output.")
    args = parser.parse_args()
    try:
        with args.archive.open("rb") as file:
            raw = file.read(MAX_ARCHIVE_BYTES + 1)
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise ValueError("private_archive_size")
        archive = json.loads(raw)
        decrypted = unseal(archive)
        result = {"decryption_verified": True, "private_content_shown": args.reveal_private}
        if args.reveal_private:
            # JSON escaping prevents response text from injecting terminal controls.
            result["private_error_text"] = decrypted.decode("utf-8", errors="replace")
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, TypeError, RecursionError):
        print('{"error":"private_archive_unavailable"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
