"""Activadores físicos: atajos de teclado globales y botones de mando.

Los dos corren en hilos propios y avisan al bucle asyncio con callbacks seguros entre hilos.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

Callback = Callable[[], None]


def _threadsafe(loop: asyncio.AbstractEventLoop, callback: Callback) -> Callback:
    def call() -> None:
        loop.call_soon_threadsafe(callback)

    return call


class HotkeyError(RuntimeError):
    pass


class Hotkeys:
    """Atajos globales con pynput. Cada atajo avisa al pulsarse y al soltarse (para mantener y hablar)."""

    def __init__(self) -> None:
        self._bindings: list[tuple[frozenset[Any], Callback | None, Callback | None]] = []
        self._pressed: set[Any] = set()
        self._active: set[int] = set()
        self._listener: Any = None
        self._keyboard: Any = None

    def bind(self, combo: str, on_press: Callback | None, on_release: Callback | None = None) -> None:
        from pynput import keyboard

        self._keyboard = keyboard
        try:
            keys = frozenset(self._canonical(k) for k in keyboard.HotKey.parse(combo))
        except ValueError as exc:
            raise HotkeyError(f"Atajo no válido '{combo}': {exc}") from exc
        if not keys:
            raise HotkeyError(f"Atajo vacío: '{combo}'")
        self._bindings.append((keys, on_press, on_release))

    def _canonical(self, key: Any) -> Any:
        keyboard = self._keyboard
        # Ctrl izquierdo y derecho cuentan igual; las letras, sin distinguir mayúsculas.
        aliases = {
            keyboard.Key.ctrl_l: keyboard.Key.ctrl,
            keyboard.Key.ctrl_r: keyboard.Key.ctrl,
            keyboard.Key.alt_l: keyboard.Key.alt,
            keyboard.Key.alt_r: keyboard.Key.alt,
            keyboard.Key.alt_gr: keyboard.Key.alt,
            keyboard.Key.shift_l: keyboard.Key.shift,
            keyboard.Key.shift_r: keyboard.Key.shift,
            keyboard.Key.cmd_l: keyboard.Key.cmd,
            keyboard.Key.cmd_r: keyboard.Key.cmd,
        }
        key = aliases.get(key, key)
        if isinstance(key, keyboard.KeyCode) and key.char:
            return keyboard.KeyCode.from_char(key.char.lower())
        return key

    def start(self) -> None:
        if not self._bindings:
            return
        from pynput import keyboard

        def press(key: Any) -> None:
            self._pressed.add(self._canonical(self._listener.canonical(key)))
            self._check()

        def release(key: Any) -> None:
            self._pressed.discard(self._canonical(self._listener.canonical(key)))
            self._check()

        self._listener = keyboard.Listener(on_press=press, on_release=release)
        self._listener.start()
        if not self._listener.wait() and not self._listener.running:
            raise HotkeyError("No se pudieron registrar los atajos (revisa los permisos de accesibilidad)")

    def _check(self) -> None:
        for index, (keys, on_press, on_release) in enumerate(self._bindings):
            down = keys <= self._pressed
            if down and index not in self._active:
                self._active.add(index)
                if on_press:
                    on_press()
            elif not down and index in self._active:
                self._active.discard(index)
                if on_release:
                    on_release()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


class Gamepad:
    """Un botón de mando con SDL2. Funciona aunque el juego tenga el foco."""

    def __init__(self, button: str, on_press: Callback, on_release: Callback) -> None:
        self._button_name = button
        self._on_press = on_press
        self._on_release = on_release
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_button: str | None = None
        self.connected: list[str] = []
        self.error: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="gamepad", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            import sdl2
        except Exception as exc:  # pragma: no cover - depende del sistema
            self.error = f"SDL2 no disponible: {exc}"
            return
        sdl2.SDL_SetHint(sdl2.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1")
        if sdl2.SDL_Init(sdl2.SDL_INIT_GAMECONTROLLER) != 0:
            self.error = f"SDL2: {sdl2.SDL_GetError().decode(errors='replace')}"
            return
        wanted = sdl2.SDL_GameControllerGetButtonFromString(self._button_name.encode()) if self._button_name else -1
        controllers: dict[int, Any] = {}
        event = sdl2.SDL_Event()
        try:
            while not self._stop.is_set():
                if not sdl2.SDL_WaitEventTimeout(event, 100):
                    continue
                kind = event.type
                if kind == sdl2.SDL_CONTROLLERDEVICEADDED:
                    pad = sdl2.SDL_GameControllerOpen(event.cdevice.which)
                    if pad:
                        joystick = sdl2.SDL_GameControllerGetJoystick(pad)
                        controllers[sdl2.SDL_JoystickInstanceID(joystick)] = pad
                        self.connected = [
                            (sdl2.SDL_GameControllerName(p) or b"?").decode(errors="replace")
                            for p in controllers.values()
                        ]
                elif kind == sdl2.SDL_CONTROLLERDEVICEREMOVED:
                    pad = controllers.pop(event.cdevice.which, None)
                    if pad:
                        sdl2.SDL_GameControllerClose(pad)
                    self.connected = [
                        (sdl2.SDL_GameControllerName(p) or b"?").decode(errors="replace") for p in controllers.values()
                    ]
                elif kind in (sdl2.SDL_CONTROLLERBUTTONDOWN, sdl2.SDL_CONTROLLERBUTTONUP):
                    button = event.cbutton.button
                    if kind == sdl2.SDL_CONTROLLERBUTTONDOWN:
                        name = sdl2.SDL_GameControllerGetStringForButton(button)
                        self.last_button = name.decode() if name else None
                    if button == wanted:
                        (self._on_press if kind == sdl2.SDL_CONTROLLERBUTTONDOWN else self._on_release)()
        finally:
            for pad in controllers.values():
                sdl2.SDL_GameControllerClose(pad)
            sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_GAMECONTROLLER)


def bind_loop(loop: asyncio.AbstractEventLoop, callback: Callback) -> Callback:
    """Envuelve un callback para que se ejecute en el bucle asyncio aunque lo llame otro hilo."""
    return _threadsafe(loop, callback)
