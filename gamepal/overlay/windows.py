"""Overlay en Windows con Win32: ventana en capas, siempre encima, sin foco y transparente a los clics.

Se excluye de cualquier captura con SetWindowDisplayAffinity, así game-pal nunca se lee a sí mismo.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from gamepal.overlay.model import ChatView, Rect, View

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
SPI_GETWORKAREA = 0x0030
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
# Chat: una ventana aparte que sí recibe el teclado.
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_VSCROLL = 0x00200000
WS_TABSTOP = 0x00010000
ES_MULTILINE = 0x0004
ES_AUTOVSCROLL = 0x0040
ES_AUTOHSCROLL = 0x0080
ES_READONLY = 0x0800
WM_SETFONT = 0x0030
WM_KEYDOWN = 0x0100
WM_CHAR = 0x0102
WM_CTLCOLOREDIT = 0x0133
WM_CTLCOLORSTATIC = 0x0138
WM_APP_CHAT = 0x8003
WM_ACTIVATE = 0x0006
EM_SETSEL = 0x00B1
EM_SCROLLCARET = 0x00B7
EM_SETCUEBANNER = 0x1501
GWLP_WNDPROC = -4
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
SW_SHOW = 5
CHAT_PADDING = 14
CHAT_INPUT_HEIGHT = 30

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
        (user32, "SystemParametersInfoW", [w.UINT, w.UINT, w.LPVOID, w.UINT], w.BOOL),
        (user32, "SendMessageW", [w.HWND, w.UINT, w.WPARAM, w.LPARAM], ctypes.c_ssize_t),
        (user32, "SetWindowTextW", [w.HWND, w.LPCWSTR], w.BOOL),
        (user32, "GetWindowTextW", [w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32, "GetWindowTextLengthW", [w.HWND], ctypes.c_int),
        (user32, "MoveWindow", [w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.BOOL], w.BOOL),
        (user32, "SetFocus", [w.HWND], w.HWND),
        (user32, "GetFocus", [], w.HWND),
        (user32, "SetForegroundWindow", [w.HWND], w.BOOL),
        (user32, "GetForegroundWindow", [], w.HWND),
        (user32, "BringWindowToTop", [w.HWND], w.BOOL),
        (user32, "IsWindowVisible", [w.HWND], w.BOOL),
        (user32, "GetWindowThreadProcessId", [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
        (user32, "AttachThreadInput", [w.DWORD, w.DWORD, w.BOOL], w.BOOL),
        (user32, "SetWindowLongPtrW", [w.HWND, ctypes.c_int, ctypes.c_void_p], ctypes.c_void_p),
        (user32, "CallWindowProcW", [ctypes.c_void_p, w.HWND, w.UINT, w.WPARAM, w.LPARAM], ctypes.c_ssize_t),
        (kernel32, "GetCurrentThreadId", [], w.DWORD),
        (gdi32, "SetBkColor", [w.HDC, w.COLORREF], w.COLORREF),
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
        # Una clase por instancia: si se registrara una sola, una segunda instancia en el mismo proceso
        # (los tests) heredaría el procedimiento de la primera, que ya puede no existir.
        self._class = f"GamePalOverlay{id(self)}"
        cls.lpszClassName = self._class
        user32.RegisterClassExW(ctypes.byref(cls))
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self._hwnd = user32.CreateWindowExW(ex, self._class, "game-pal", WS_POPUP, 0, 0, 400, 100, None,
                                            None, instance, None)  # fmt: skip
        if not self._hwnd:
            raise OSError("No se pudo crear la ventana del overlay")
        user32.SetWindowDisplayAffinity(self._hwnd, WDA_EXCLUDEFROMCAPTURE)
        self._fonts: dict[tuple[int, bool], Any] = {}
        self._background = gdi32.CreateSolidBrush(_rgb(8, 26, 30))
        self._create_chat(instance)

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

    # Chat -----------------------------------------------------------------------------------

    def _create_chat(self, instance: Any) -> None:
        """Ventana del chat: historial, estado, campo para escribir y ayuda. Esta sí recibe el teclado."""
        self._chat_view: ChatView | None = None
        self._chat_shown = False
        self._chat_text = ""
        self._previous: Any = None
        self._submit: Callable[[str], None] = lambda text: None
        self._close: Callable[[], None] = lambda: None
        self._cue: Any = None
        self._input_background = gdi32.CreateSolidBrush(_rgb(18, 49, 56))
        self._chat_proc = WNDPROC(self._chat_wndproc)
        cls = WNDCLASSEXW()
        cls.cbSize = ctypes.sizeof(WNDCLASSEXW)
        cls.lpfnWndProc = self._chat_proc
        cls.hInstance = instance
        cls.hbrBackground = self._background
        cls.lpszClassName = f"{self._class}Chat"
        user32.RegisterClassExW(ctypes.byref(cls))
        ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        self._chat = user32.CreateWindowExW(ex, f"{self._class}Chat", "game-pal", WS_POPUP, 0, 0, 560, 480, None, None,
                                            instance, None)  # fmt: skip
        if not self._chat:
            raise OSError("No se pudo crear la ventana del chat")
        user32.SetWindowDisplayAffinity(self._chat, WDA_EXCLUDEFROMCAPTURE)

        def child(kind: str, style: int) -> Any:
            return user32.CreateWindowExW(0, kind, "", WS_CHILD | WS_VISIBLE | style, 0, 0, 10, 10, self._chat, None,
                                          instance, None)  # fmt: skip

        self._chat_title = child("STATIC", 0)
        self._history = child("EDIT", WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL)
        self._status = child("STATIC", 0)
        self._input = child("EDIT", WS_TABSTOP | ES_AUTOHSCROLL)
        self._hint = child("STATIC", 0)
        user32.SetWindowTextW(self._chat_title, "GAME-PAL")
        # Intro y Esc llegan al campo, no a la ventana: se intercepta su procedimiento.
        self._input_proc = WNDPROC(self._input_wndproc)
        self._input_default = user32.SetWindowLongPtrW(
            self._input, GWLP_WNDPROC, ctypes.cast(self._input_proc, ctypes.c_void_p)
        )

    def bind_chat(self, submit: Callable[[str], None], close: Callable[[], None]) -> None:
        self._submit, self._close = submit, close

    def render_chat(self, chat: ChatView | None) -> None:
        with self._lock:
            self._chat_view = chat
        user32.PostMessageW(self._chat, WM_APP_CHAT, 0, 0)

    def _input_text(self) -> str:
        length = user32.GetWindowTextLengthW(self._input)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(self._input, buffer, length + 1)
        return buffer.value

    def _input_wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_KEYDOWN and wparam == VK_RETURN:
            text = self._input_text()
            user32.SetWindowTextW(self._input, "")
            if text.strip():
                self._submit(text)
            return 0
        if msg == WM_KEYDOWN and wparam == VK_ESCAPE:
            self._close()
            return 0
        if msg == WM_CHAR and wparam in (VK_RETURN, VK_ESCAPE):
            return 0  # sin el pitido de un campo de una línea
        return int(user32.CallWindowProcW(self._input_default, hwnd, msg, wparam, lparam))

    def _chat_wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_APP_CHAT:
            self._apply_chat()
            return 0
        if msg in (WM_CTLCOLOREDIT, WM_CTLCOLORSTATIC):
            colors = {
                self._chat_title: _rgb(126, 224, 185),
                self._status: _rgb(224, 196, 142),
                self._hint: _rgb(143, 176, 170),
            }
            gdi32.SetTextColor(wparam, colors.get(lparam, _rgb(235, 245, 240)))
            typing = lparam == self._input
            gdi32.SetBkColor(wparam, _rgb(18, 49, 56) if typing else _rgb(8, 26, 30))
            return int(self._input_background if typing else self._background)
        return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))

    @staticmethod
    def _history_text(chat: ChatView) -> str:
        if not chat.entries:
            return chat.empty
        blocks = []
        for entry in chat.entries:
            mark = "💡" if entry.kind == "proactive" else "›"
            answer = entry.answer or ("…" if not entry.done else "")
            blocks.append(f"{mark} {entry.question}\r\n{answer}")
        return "\r\n\r\n".join(blocks).replace("\r\n", "\n").replace("\n", "\r\n")

    def _apply_chat(self) -> None:
        with self._lock:
            chat = self._chat_view
        if chat is None:
            if self._chat_shown:
                user32.ShowWindow(self._chat, SW_HIDE)
                # El teclado vuelve al juego.
                if self._previous and user32.IsWindow(self._previous):
                    user32.SetForegroundWindow(self._previous)
            self._chat_shown = False
            return
        scale = chat.font_scale
        width, height, pad = chat.width, chat.height, CHAT_PADDING
        inner = width - 2 * pad
        line = round(18 * scale)
        field = round(CHAT_INPUT_HEIGHT * scale)
        hint_y = height - pad - line
        field_y = hint_y - 6 - field
        status_y = field_y - 6 - line
        top = pad + line + 6
        bottom = status_y - 6 if chat.status else field_y - 8
        for hwnd, size, bold in (
            (self._chat_title, 12, True),
            (self._history, 15, False),
            (self._status, 13, False),
            (self._input, 15, False),
            (self._hint, 12, False),
        ):
            user32.SendMessageW(hwnd, WM_SETFONT, self._font(round(size * scale), bold), 1)
        user32.MoveWindow(self._chat_title, pad, pad, inner, line, True)
        user32.MoveWindow(self._history, pad, top, inner, max(40, bottom - top), True)
        user32.MoveWindow(self._status, pad, status_y, inner, line, True)
        user32.ShowWindow(self._status, SW_SHOWNOACTIVATE if chat.status else SW_HIDE)
        user32.MoveWindow(self._input, pad, field_y, inner, field, True)
        user32.MoveWindow(self._hint, pad, hint_y, inner, line, True)
        user32.SetWindowTextW(self._status, chat.status)
        user32.SetWindowTextW(self._hint, chat.hint)
        # Necesita que el buffer siga vivo mientras el campo lo use.
        self._cue = ctypes.create_unicode_buffer(chat.placeholder)
        user32.SendMessageW(self._input, EM_SETCUEBANNER, 1, ctypes.addressof(self._cue))
        text = self._history_text(chat)
        if text != self._chat_text:
            self._chat_text = text
            user32.SetWindowTextW(self._history, text)
            end = user32.GetWindowTextLengthW(self._history)
            user32.SendMessageW(self._history, EM_SETSEL, end, end)
            user32.SendMessageW(self._history, EM_SCROLLCARET, 0, 0)
        region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, 16, 16)
        user32.SetWindowRgn(self._chat, region, True)
        user32.SetLayeredWindowAttributes(self._chat, 0, round(255 * chat.opacity), LWA_ALPHA)
        user32.SetWindowPos(self._chat, HWND_TOPMOST, chat.x, chat.y, width, height, SWP_SHOWWINDOW)
        if not self._chat_shown:
            self._chat_shown = True
            self._focus_chat()

    def _focus_chat(self) -> None:
        """Lleva el teclado al chat aunque el juego esté delante.

        Windows solo deja pasar a primer plano a quien recibió la última pulsación (el juego): uniendo un
        momento la entrada de los dos hilos, el sistema acepta el cambio.
        """
        foreground = user32.GetForegroundWindow()
        self._previous = foreground if foreground and foreground != self._chat else None
        current = kernel32.GetCurrentThreadId()
        other = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        attached = bool(other and other != current and user32.AttachThreadInput(current, other, True))
        try:
            user32.BringWindowToTop(self._chat)
            user32.SetForegroundWindow(self._chat)
            user32.SetFocus(self._input)
        finally:
            if attached:
                user32.AttachThreadInput(current, other, False)

    def screen(self) -> Rect | None:
        """Zona de trabajo del monitor principal (sin la barra de tareas), en píxeles reales."""
        area = wintypes.RECT()
        if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0):
            return None
        return Rect(area.left, area.top, area.right - area.left, area.bottom - area.top)

    def run(self) -> None:
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def quit(self) -> None:
        user32.PostMessageW(self._hwnd, WM_APP_QUIT, 0, 0)
