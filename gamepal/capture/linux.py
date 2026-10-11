"""Captura en Linux sobre X11 (incluido XWayland).

Wayland puro no permite a una aplicación leer otras ventanas sin el portal de escritorio;
ese backend llegará después. Mientras tanto se informa con un error claro.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import numpy as np

from gamepal.capture.base import CaptureUnavailableError, RGBImage, WindowInfo


class X11Capture:
    name = "x11"

    def __init__(self) -> None:
        if not os.environ.get("DISPLAY"):
            if os.environ.get("WAYLAND_DISPLAY"):
                raise CaptureUnavailableError(
                    "Sesión Wayland sin XWayland: la captura por portal aún no está disponible. "
                    "Inicia sesión en X11 o activa XWayland."
                )
            raise CaptureUnavailableError("No hay servidor gráfico (DISPLAY vacío)")
        try:
            from Xlib import X, display
        except ImportError as exc:  # pragma: no cover
            raise CaptureUnavailableError("Falta python-xlib") from exc
        self._x = X
        self._display: Any = display.Display()
        self._root: Any = self._display.screen().root
        self._atoms = {
            name: self._display.intern_atom(name)
            for name in ("_NET_CLIENT_LIST_STACKING", "_NET_ACTIVE_WINDOW", "_NET_WM_NAME", "_NET_WM_PID")
        }
        self._lock = threading.Lock()
        self._local = threading.local()

    def _mss(self) -> Any:
        instance = getattr(self._local, "mss", None)
        if instance is None:
            import mss

            instance = mss.mss()
            self._local.mss = instance
        return instance

    def _prop(self, window: Any, name: str) -> Any:
        prop = window.get_full_property(self._atoms[name], self._x.AnyPropertyType)
        return prop.value if prop else None

    def _title(self, window: Any) -> str:
        value = self._prop(window, "_NET_WM_NAME")
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        name = window.get_wm_name()
        return name if isinstance(name, str) else ""

    def list_windows(self) -> list[WindowInfo]:
        with self._lock:
            ids = self._prop(self._root, "_NET_CLIENT_LIST_STACKING")
            if ids is None:
                raise CaptureUnavailableError("El gestor de ventanas no expone _NET_CLIENT_LIST_STACKING")
            result: list[WindowInfo] = []
            for wid in reversed(list(ids)):  # la lista va de atrás a delante
                window = self._display.create_resource_object("window", wid)
                try:
                    geometry = window.get_geometry()
                    origin = window.translate_coords(self._root, 0, 0)
                    pid_value = self._prop(window, "_NET_WM_PID")
                except Exception:  # noqa: S112 - la ventana pudo cerrarse durante la consulta
                    continue
                pid = int(pid_value[0]) if pid_value is not None and len(pid_value) else 0
                result.append(
                    WindowInfo(
                        id=int(wid),
                        title=self._title(window),
                        process=_process_name(pid),
                        pid=pid,
                        width=int(geometry.width),
                        height=int(geometry.height),
                        left=-int(origin.x),
                        top=-int(origin.y),
                    )
                )
            return result

    def foreground_pid(self) -> int | None:
        with self._lock:
            active = self._prop(self._root, "_NET_ACTIVE_WINDOW")
            if active is None or not len(active) or not active[0]:
                return None
            window = self._display.create_resource_object("window", int(active[0]))
            try:
                pid_value = self._prop(window, "_NET_WM_PID")
            except Exception:
                return None
            return int(pid_value[0]) if pid_value is not None and len(pid_value) else None

    def grab(self, window: WindowInfo) -> RGBImage | None:
        current = {w.id: w for w in self.list_windows()}.get(window.id)
        if current is None:
            return None
        shot = self._mss().grab(
            {"left": current.left, "top": current.top, "width": current.width, "height": current.height}
        )
        bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return np.ascontiguousarray(bgra[:, :, 2::-1])


def _process_name(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
