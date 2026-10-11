"""Proceso del overlay. Lo lanza el daemon con su dirección y token en variables de entorno."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from typing import Protocol

from gamepal.overlay.client import OverlayClient
from gamepal.overlay.model import Rect, View

log = logging.getLogger("gamepal.overlay")


class Backend(Protocol):
    def render(self, view: View | None) -> None: ...

    def screen(self) -> Rect | None:
        """Zona útil de la pantalla principal, con origen arriba a la izquierda. None: no se sabe."""
        ...

    def run(self) -> None: ...

    def quit(self) -> None: ...


def create_backend() -> Backend:
    if sys.platform == "darwin":
        from gamepal.overlay.macos import MacOverlay

        return MacOverlay()
    if sys.platform == "win32":
        from gamepal.overlay.windows import WindowsOverlay

        return WindowsOverlay()
    from gamepal.overlay.tk import TkOverlay

    return TkOverlay()


PARENT_CHECK_S = 2.0


def _alive_windows(pid: int) -> bool:
    synchronize, wait_object_0 = 0x00100000, 0
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.WaitForSingleObject(handle, 0) != wait_object_0)
    finally:
        kernel32.CloseHandle(handle)


def watch_parent(pid: int, interval: float = PARENT_CHECK_S) -> None:
    """Cierra el overlay si game-pal ya no existe: si se cayó de golpe, no debe quedar una ventana huérfana."""
    original = os.getppid()
    while True:
        time.sleep(interval)
        if sys.platform == "win32":
            alive = _alive_windows(pid)
        else:
            # Al morir el padre, el proceso pasa a depender de otro (launchd, init): cambia su padre.
            alive = os.getppid() == original
        if not alive:
            log.info("game-pal ya no está en marcha; se cierra el overlay")
            os._exit(0)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s overlay %(levelname)s: %(message)s")
    http_url = os.environ.get("GAMEPAL_HTTP", "")
    ws_url = os.environ.get("GAMEPAL_WS", "")
    token = os.environ.pop("GAMEPAL_TOKEN", "")
    if not (http_url and ws_url and token):
        print("El overlay lo arranca game-pal; no se ejecuta a mano.", file=sys.stderr)
        return 2
    try:
        backend = create_backend()
    except Exception as exc:
        print(f"Overlay no disponible en este sistema: {exc}", file=sys.stderr)
        return 3
    try:
        screen = backend.screen()
    except Exception:
        log.warning("No se pudo leer el tamaño de la pantalla", exc_info=True)
        screen = None
    client = OverlayClient(http_url, ws_url, token, backend.render, screen)
    worker = threading.Thread(target=client.run, name="overlay-client", daemon=True)
    worker.start()
    parent = os.environ.get("GAMEPAL_PARENT_PID", "")
    if parent.isdigit():
        threading.Thread(target=watch_parent, args=(int(parent),), name="overlay-parent", daemon=True).start()
    try:
        backend.run()
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
