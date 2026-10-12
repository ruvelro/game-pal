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


def test_windows_chat_takes_the_keyboard_and_sends_with_enter() -> None:
    from gamepal.overlay.model import ChatEntry, ChatView
    from gamepal.overlay.windows import VK_ESCAPE, VK_RETURN, WM_KEYDOWN, WindowsOverlay, user32

    overlay = WindowsOverlay()
    sent: list[str] = []
    closed: list[bool] = []
    overlay.bind_chat(sent.append, lambda: closed.append(True))
    chat = ChatView(entries=(ChatEntry("question", "¿Qué planto?", "Arándanos."),), status="", placeholder="Pregunta…",
                    hint="Intro envía · Esc cierra", empty="", x=120, y=90, width=480, height=360, opacity=0.95,
                    font_scale=1.0)  # fmt: skip
    overlay.render_chat(chat)

    def type_and_send() -> None:
        user32.SetWindowTextW(overlay._input, "¿Y en otoño?")
        user32.SendMessageW(overlay._input, WM_KEYDOWN, VK_RETURN, 0)
        user32.SendMessageW(overlay._input, WM_KEYDOWN, VK_ESCAPE, 0)
        overlay.quit()

    threading.Timer(1.0, overlay.quit).start()
    overlay.run()
    # Las ventanas siguen vivas tras salir del bucle: se le escribe desde este mismo hilo.
    assert user32.IsWindowVisible(overlay._chat)
    rect = wintypes.RECT()
    user32.GetWindowRect(overlay._chat, ctypes.byref(rect))
    assert (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top) == (120, 90, 480, 360)
    affinity = wintypes.DWORD()
    user32.GetWindowDisplayAffinity(overlay._chat, ctypes.byref(affinity))
    assert affinity.value == 0x11
    history = ctypes.create_unicode_buffer(400)
    user32.GetWindowTextW(overlay._history, history, 400)
    assert "Arándanos." in history.value
    type_and_send()
    assert sent == ["¿Y en otoño?"]
    assert closed == [True]
    length = user32.GetWindowTextLengthW(overlay._input)
    assert length == 0
