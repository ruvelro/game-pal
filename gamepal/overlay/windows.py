"""Overlay en Windows con Win32: ventana en capas, siempre encima, sin foco y transparente a los clics.

Se excluye de cualquier captura con SetWindowDisplayAffinity, así game-pal nunca se lee a sí mismo.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Any

from gamepal.overlay.model import View

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("Este módulo solo funciona en Windows")

# Instancias propias de las DLL: así declarar tipos aquí no afecta a otros módulos que usan ctypes.windll.
user32: Any = ctypes.WinDLL("user32", use_last_error=True)
gdi32: Any = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
LWA_ALPHA = 0x2
WDA_EXCLUDEFROMCAPTURE = 0x11
WM_PAINT = 0x000F
WM_DESTROY = 0x0002
WM_APP_UPDATE = 0x8001
WM_APP_QUIT = 0x8002
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
DT_WORDBREAK = 0x0010
DT_CALCRECT = 0x0400
DT_NOPREFIX = 0x0800
TRANSPARENT_BK = 1
PADDING = 16

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


def _rgb(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16)


def _declare() -> None:
    """Tipos de todas las funciones Win32 que se usan.

    Sin ellos ctypes pasa los enteros como int de 32 bits: en Windows de 64 bits un handle o
    HWND_TOPMOST (-1) llega truncado y las llamadas fallan en silencio.
    """
    w = wintypes
    lp_rect = ctypes.POINTER(w.RECT)
    signatures: list[tuple[Any, str, list[Any], Any]] = [
        (user32, "DefWindowProcW", [w.HWND, w.UINT, w.WPARAM, w.LPARAM], ctypes.c_ssize_t),
        (user32, "RegisterClassExW", [ctypes.POINTER(WNDCLASSEXW)], w.ATOM),
        (user32, "CreateWindowExW", [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID], w.HWND),
        (user32, "SetWindowDisplayAffinity", [w.HWND, w.DWORD], w.BOOL),
        (user32, "GetWindowDisplayAffinity", [w.HWND, ctypes.POINTER(w.DWORD)], w.BOOL),
        (user32, "PostMessageW", [w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL),
        (user32, "GetDC", [w.HWND], w.HDC),
        (user32, "ReleaseDC", [w.HWND, w.HDC], ctypes.c_int),
        (user32, "DrawTextW", [w.HDC, w.LPCWSTR, ctypes.c_int, lp_rect, w.UINT], ctypes.c_int),
        (user32, "ShowWindow", [w.HWND, ctypes.c_int], w.BOOL),
        (user32, "SetWindowRgn", [w.HWND, w.HRGN, w.BOOL], ctypes.c_int),
        (user32, "SetLayeredWindowAttributes", [w.HWND, w.COLORREF, w.BYTE, w.DWORD], w.BOOL),
        (user32, "SetWindowPos", [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  w.UINT], w.BOOL),
        (user32, "InvalidateRect", [w.HWND, lp_rect, w.BOOL], w.BOOL),
        (user32, "BeginPaint", [w.HWND, ctypes.POINTER(PAINTSTRUCT)], w.HDC),
        (user32, "EndPaint", [w.HWND, ctypes.POINTER(PAINTSTRUCT)], w.BOOL),
        (user32, "GetClientRect", [w.HWND, lp_rect], w.BOOL),
        (user32, "GetWindowRect", [w.HWND, lp_rect], w.BOOL),
        (user32, "GetWindowLongW", [w.HWND, ctypes.c_int], w.LONG),
        (user32, "IsWindow", [w.HWND], w.BOOL),
        (user32, "FillRect", [w.HDC, lp_rect, w.HBRUSH], ctypes.c_int),
        (user32, "GetMessageW", [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], w.BOOL),
        (user32, "TranslateMessage", [ctypes.POINTER(w.MSG)], w.BOOL),
        (user32, "DispatchMessageW", [ctypes.POINTER(w.MSG)], ctypes.c_ssize_t),
        (user32, "PostQuitMessage", [ctypes.c_int], None),
        (kernel32, "GetModuleHandleW", [w.LPCWSTR], w.HMODULE),
        (gdi32, "CreateFontW", [ctypes.c_int] * 5 + [w.DWORD] * 8 + [w.LPCWSTR], w.HFONT),
        (gdi32, "CreateSolidBrush", [w.COLORREF], w.HBRUSH),
        (gdi32, "CreateRoundRectRgn", [ctypes.c_int] * 6, w.HRGN),
        (gdi32, "SelectObject", [w.HDC, w.HGDIOBJ], w.HGDIOBJ),
        (gdi32, "SetBkMode", [w.HDC, ctypes.c_int], ctypes.c_int),
        (gdi32, "SetTextColor", [w.HDC, w.COLORREF], w.COLORREF),
    ]  # fmt: skip
    for dll, name, args, result in signatures:
        function = getattr(dll, name)
        function.argtypes = args
        function.restype = result


_declare()


class WindowsOverlay:
    def __init__(self) -> None:
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            user32.SetProcessDPIAware()
        self._lock = threading.Lock()
        self._view: View | None = None
        self._blocks: list[tuple[str, Any, int, wintypes.RECT]] = []
        self._proc = WNDPROC(self._wndproc)
        instance = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSEXW()
        cls.cbSize = ctypes.sizeof(WNDCLASSEXW)
        cls.lpfnWndProc = self._proc
        cls.hInstance = instance
        cls.lpszClassName = "GamePalOverlay"
        user32.RegisterClassExW(ctypes.byref(cls))
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self._hwnd = user32.CreateWindowExW(ex, "GamePalOverlay", "game-pal", WS_POPUP, 0, 0, 400, 100, None,
                                            None, instance, None)  # fmt: skip
        if not self._hwnd:
            raise OSError("No se pudo crear la ventana del overlay")
        user32.SetWindowDisplayAffinity(self._hwnd, WDA_EXCLUDEFROMCAPTURE)
        self._fonts: dict[tuple[int, bool], Any] = {}
        self._background = gdi32.CreateSolidBrush(_rgb(8, 26, 30))

    def _font(self, size: int, bold: bool) -> Any:
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = gdi32.CreateFontW(-size, 0, 0, 0, 700 if bold else 400, 0, 0, 0, 1, 0, 0, 5, 0,
                                                 "Segoe UI")  # fmt: skip
        return self._fonts[key]

    def render(self, view: View | None) -> None:
        with self._lock:
            self._view = view
        user32.PostMessageW(self._hwnd, WM_APP_UPDATE, 0, 0)

    def _layout(self, view: View) -> int:
        """Calcula los bloques de texto y devuelve la altura total."""
        dc = user32.GetDC(self._hwnd)
        inner = view.width - 2 * PADDING
        y = PADDING
        blocks = []
        for text, size, bold, color in (
            (view.title.upper(), round(12 * view.font_scale), True, _rgb(126, 224, 185)),
            (view.body, round(16 * view.font_scale), False, _rgb(235, 245, 240)),
            (view.status, round(13 * view.font_scale), False, _rgb(224, 196, 142)),
        ):
            if not text:
                continue
            font = self._font(size, bold)
            gdi32.SelectObject(dc, font)
            rect = wintypes.RECT(PADDING, y, PADDING + inner, y + 10_000)
            user32.DrawTextW(dc, text, -1, ctypes.byref(rect), DT_WORDBREAK | DT_CALCRECT | DT_NOPREFIX)
            blocks.append((text, font, color, wintypes.RECT(PADDING, y, PADDING + inner, rect.bottom)))
            y = rect.bottom + 6
        user32.ReleaseDC(self._hwnd, dc)
        self._blocks = blocks
        return y - 6 + PADDING

    def _apply(self) -> None:
        with self._lock:
            view = self._view
        if view is None:
            user32.ShowWindow(self._hwnd, SW_HIDE)
            return
        height = self._layout(view)
        top = view.y - height if view.anchor_bottom else view.y
        region = gdi32.CreateRoundRectRgn(0, 0, view.width + 1, height + 1, 16, 16)
        user32.SetWindowRgn(self._hwnd, region, True)
        user32.SetLayeredWindowAttributes(self._hwnd, 0, round(255 * view.opacity), LWA_ALPHA)
        user32.SetWindowPos(self._hwnd, HWND_TOPMOST, view.x, top, view.width, height,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)  # fmt: skip
        user32.InvalidateRect(self._hwnd, None, True)

    def _paint(self) -> None:
        ps = PAINTSTRUCT()
        dc = user32.BeginPaint(self._hwnd, ctypes.byref(ps))
        rect = wintypes.RECT()
        user32.GetClientRect(self._hwnd, ctypes.byref(rect))
        user32.FillRect(dc, ctypes.byref(rect), self._background)
        gdi32.SetBkMode(dc, TRANSPARENT_BK)
        for text, font, color, box in self._blocks:
            gdi32.SelectObject(dc, font)
            gdi32.SetTextColor(dc, color)
            user32.DrawTextW(dc, text, -1, ctypes.byref(box), DT_WORDBREAK | DT_NOPREFIX)
        user32.EndPaint(self._hwnd, ctypes.byref(ps))

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_APP_UPDATE:
            self._apply()
            return 0
        if msg == WM_PAINT:
            self._paint()
            return 0
        if msg in (WM_APP_QUIT, WM_DESTROY):
            user32.PostQuitMessage(0)
            return 0
        return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))

    def run(self) -> None:
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def quit(self) -> None:
        user32.PostMessageW(self._hwnd, WM_APP_QUIT, 0, 0)
