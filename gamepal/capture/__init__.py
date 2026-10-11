"""Captura de la ventana del juego, con un backend por sistema operativo."""

from __future__ import annotations

import sys

from gamepal.capture.base import (
    CaptureBackend,
    CaptureUnavailableError,
    Frame,
    RGBImage,
    WindowInfo,
    downscale,
    match_window,
)

__all__ = [
    "CaptureBackend",
    "CaptureUnavailableError",
    "Frame",
    "RGBImage",
    "WindowInfo",
    "create_backend",
    "downscale",
    "match_window",
]


def create_backend() -> CaptureBackend:
    if sys.platform == "darwin":
        from gamepal.capture.macos import MacCapture

        return MacCapture()
    if sys.platform == "win32":
        from gamepal.capture.windows import WindowsCapture

        return WindowsCapture()
    if sys.platform.startswith("linux"):
        from gamepal.capture.linux import X11Capture

        return X11Capture()
    raise CaptureUnavailableError(f"Sistema no soportado: {sys.platform}")
