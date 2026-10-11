"""Captura en Windows (x64 y ARM64) con Win32 vía ctypes.

Enumera ventanas con EnumWindows y captura el área cliente de la ventana del juego.
Requiere el juego en modo ventana o ventana sin bordes.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Any

import numpy as np

from gamepal.capture.base import CaptureUnavailableError, RGBImage, WindowInfo

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("Este módulo solo funciona en Windows")

_user32: Any = ctypes.windll.user32
_kernel32: Any = ctypes.windll.kernel32
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _make_dpi_aware() -> None:
    try:
        _user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except (AttributeError, OSError):
        _user32.SetProcessDPIAware()


def _process_name(pid: int) -> str:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def _client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top


class WindowsCapture:
    name = "win32"

    def __init__(self) -> None:
        _make_dpi_aware()
        self._local = threading.local()

    def _mss(self) -> Any:
        # mss no es seguro entre hilos: una instancia por hilo.
        instance = getattr(self._local, "mss", None)
        if instance is None:
            import mss

            instance = mss.mss()
            self._local.mss = instance
        return instance

    def list_windows(self) -> list[WindowInfo]:
        found: list[WindowInfo] = []

        def visit(hwnd: int, _: int) -> bool:
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                return True
            if _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
                return True
            length = _user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, title, length + 1)
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rect = _client_rect(hwnd)
            if rect is None:
                return True
            left, top, width, height = rect
            found.append(
                WindowInfo(
                    id=int(hwnd),
                    title=title.value,
                    process=_process_name(pid.value),
                    pid=int(pid.value),
                    width=width,
                    height=height,
                    left=left,
                    top=top,
                )
            )
            return True

        if not _user32.EnumWindows(_EnumProc(visit), 0):
            raise CaptureUnavailableError("EnumWindows falló")
        return found

    def foreground_pid(self) -> int | None:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) or None

    def grab(self, window: WindowInfo) -> RGBImage | None:
        if not _user32.IsWindow(window.id):
            return None
        rect = _client_rect(window.id)
        if rect is None:
            return None
        left, top, width, height = rect
        if width < 1 or height < 1:
            return None
        shot = self._mss().grab({"left": left, "top": top, "width": width, "height": height})
        bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return np.ascontiguousarray(bgra[:, :, 2::-1])
