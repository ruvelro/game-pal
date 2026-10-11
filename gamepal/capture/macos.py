"""Captura en macOS con ScreenCaptureKit: solo la ventana del juego, nunca la pantalla entera."""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from gamepal.capture.base import CaptureUnavailableError, RGBImage, WindowInfo

_TIMEOUT_S = 5.0
_CONTENT_TTL_S = 2.0


# Lo que se usa de Quartz. PyObjC resuelve cada nombre la primera vez que se pide, y esa carga no es segura
# entre hilos: dos hilos a la vez (captura y detección del juego) daban KeyError. Se resuelven todos al crear
# la captura, en un solo hilo.
_QUARTZ_NAMES = (
    "CGWindowListCopyWindowInfo", "kCGWindowListOptionOnScreenOnly", "kCGWindowListExcludeDesktopElements",
    "kCGNullWindowID", "CGImageGetWidth", "CGImageGetHeight", "CGImageGetBytesPerRow", "CGDataProviderCopyData",
    "CGImageGetDataProvider",
)  # fmt: skip


class _Quartz:
    def __init__(self, module: Any) -> None:
        for name in _QUARTZ_NAMES:
            setattr(self, name, getattr(module, name))


class MacCapture:
    name = "screencapturekit"

    def __init__(self) -> None:
        try:
            import AppKit
            import Quartz
            import ScreenCaptureKit
        except ImportError as exc:  # pragma: no cover - depende del sistema
            raise CaptureUnavailableError("Falta pyobjc-framework-ScreenCaptureKit") from exc
        # ScreenCaptureKit necesita una sesión de CoreGraphics inicializada.
        AppKit.NSApplication.sharedApplication()
        self._quartz: Any = _Quartz(Quartz)
        self._sck: Any = ScreenCaptureKit
        self._lock = threading.Lock()
        self._content: Any = None
        self._content_at = 0.0
        if not Quartz.CGPreflightScreenCaptureAccess():
            Quartz.CGRequestScreenCaptureAccess()
            raise CaptureUnavailableError(
                "macOS necesita permiso de Grabación de pantalla para game-pal. "
                "Actívalo en Ajustes del Sistema > Privacidad y seguridad y reinicia la app."
            )

    def _call(self, start: Any) -> tuple[Any, Any]:
        done = threading.Event()
        box: dict[str, Any] = {}

        def handler(result: Any, error: Any) -> None:
            box["result"], box["error"] = result, error
            done.set()

        start(handler)
        if not done.wait(_TIMEOUT_S):
            raise TimeoutError("ScreenCaptureKit no respondió a tiempo")
        return box.get("result"), box.get("error")

    def _shareable(self, refresh: bool = False) -> Any:
        with self._lock:
            now = time.monotonic()
            if refresh or self._content is None or now - self._content_at > _CONTENT_TTL_S:
                content, error = self._call(
                    lambda h: (
                        self._sck.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                            True, True, h
                        )
                    )
                )
                if error is not None or content is None:
                    raise CaptureUnavailableError(f"No se pudo listar ventanas: {error}")
                self._content, self._content_at = content, now
            return self._content

    def list_windows(self) -> list[WindowInfo]:
        windows: list[WindowInfo] = []
        for w in self._shareable().windows():
            app = w.owningApplication()
            if app is None or w.windowLayer() != 0:
                continue
            frame = w.frame()
            windows.append(
                WindowInfo(
                    id=int(w.windowID()),
                    title=str(w.title() or ""),
                    process=str(app.applicationName() or ""),
                    pid=int(app.processID()),
                    width=int(frame.size.width),
                    height=int(frame.size.height),
                    left=int(frame.origin.x),
                    top=int(frame.origin.y),
                )
            )
        return windows

    def foreground_pid(self) -> int | None:
        # La lista de Quartz va de delante a atrás; la primera ventana normal es la activa.
        q = self._quartz
        info = q.CGWindowListCopyWindowInfo(
            q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements, q.kCGNullWindowID
        )
        for entry in info or []:
            if entry.get("kCGWindowLayer") == 0:
                pid = entry.get("kCGWindowOwnerPID")
                return int(pid) if pid is not None else None
        return None

    def _find(self, window_id: int, refresh: bool) -> Any:
        for w in self._shareable(refresh).windows():
            if int(w.windowID()) == window_id:
                return w
        return None

    def grab(self, window: WindowInfo) -> RGBImage | None:
        target = self._find(window.id, refresh=False) or self._find(window.id, refresh=True)
        if target is None:
            return None
        sck, q = self._sck, self._quartz
        content_filter = sck.SCContentFilter.alloc().initWithDesktopIndependentWindow_(target)
        scale = float(content_filter.pointPixelScale())
        rect = content_filter.contentRect()
        config = sck.SCStreamConfiguration.alloc().init()
        config.setWidth_(max(1, int(rect.size.width * scale)))
        config.setHeight_(max(1, int(rect.size.height * scale)))
        config.setShowsCursor_(False)
        image, error = self._call(
            lambda h: sck.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                content_filter, config, h
            )
        )
        if error is not None or image is None:
            return None
        width, height = q.CGImageGetWidth(image), q.CGImageGetHeight(image)
        row_bytes = q.CGImageGetBytesPerRow(image)
        data = q.CGDataProviderCopyData(q.CGImageGetDataProvider(image))
        raw = np.frombuffer(data, dtype=np.uint8)[: height * row_bytes]
        bgra = raw.reshape(height, row_bytes)[:, : width * 4].reshape(height, width, 4)
        return np.ascontiguousarray(bgra[:, :, 2::-1])
