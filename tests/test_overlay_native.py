"""Prueba del overlay nativo de Windows en una sesión real (solo se ejecuta en Windows)."""

import ctypes
import sys
import threading
from ctypes import wintypes

import pytest

from gamepal.overlay.model import View

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Overlay de Windows")


def test_windows_overlay_window_is_placed_and_hidden_from_capture() -> None:
    from gamepal.overlay.windows import WDA_EXCLUDEFROMCAPTURE, WindowsOverlay, user32

    overlay = WindowsOverlay()
    view = View(title="game-pal", body="Coge el Rayo.", status="", x=100, y=80, width=360,
                anchor_bottom=False, opacity=0.9, font_scale=1.0)  # fmt: skip
    overlay.render(view)
    threading.Timer(0.8, overlay.quit).start()
    overlay.run()
    hwnd = overlay._hwnd
    assert user32.IsWindow(hwnd)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    assert (rect.left, rect.top, rect.right - rect.left) == (100, 80, 360)
    assert rect.bottom - rect.top > 40
    affinity = wintypes.DWORD()
    user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
    assert affinity.value == WDA_EXCLUDEFROMCAPTURE
    style = user32.GetWindowLongW(hwnd, -20)
    assert style & 0x20  # WS_EX_TRANSPARENT: los clics pasan al juego
    assert style & 0x08000000  # WS_EX_NOACTIVATE: nunca roba el foco
