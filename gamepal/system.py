"""Datos del equipo que sirven para elegir buenos valores por defecto: idioma y memoria."""

from __future__ import annotations

import ctypes
import os
import sys

# Idiomas que game-pal sabe usar para responder (los mismos que ofrece la GUI).
SUPPORTED_LANGUAGES = ("es", "en", "fr", "de", "it", "pt", "ja", "ko", "zh", "ru", "pl")


def _code(value: str | None) -> str | None:
    """«es_ES.UTF-8», «es-419» o «Spanish» → «es», si es un idioma soportado."""
    if not value:
        return None
    code = value.split(":")[0].split(".")[0].replace("-", "_").split("_")[0].lower()
    return code if code in SUPPORTED_LANGUAGES else None


def _os_language() -> str | None:
    if sys.platform == "darwin":
        try:
            from Foundation import NSLocale

            preferred = NSLocale.preferredLanguages()
            return str(preferred[0]) if preferred else None
        except Exception:
            return None
    if sys.platform == "win32":
        buffer = ctypes.create_unicode_buffer(85)
        try:
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):  # type: ignore[attr-defined]
                return buffer.value
        except (AttributeError, OSError):
            return None
    return None


def system_language() -> str | None:
    """El idioma del sistema, si game-pal lo soporta. En macOS y Windows manda el del sistema, no el de la terminal."""
    language = _code(_os_language())
    if language:
        return language
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var, "")
        if value and value != "POSIX" and not value.startswith("C"):
            return _code(value)
    return None


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def total_memory_gb() -> float | None:
    """Memoria RAM total. En Apple Silicon es también la memoria de la GPU."""
    if sys.platform == "win32":
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return float(status.ullTotalPhys) / 1024**3
        except (AttributeError, OSError):
            return None
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (ValueError, OSError, AttributeError):
        return None
