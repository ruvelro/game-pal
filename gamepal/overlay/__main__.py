"""Proceso del overlay. Lo lanza el daemon con su dirección y token en variables de entorno."""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Protocol

from gamepal.overlay.client import OverlayClient
from gamepal.overlay.model import View

log = logging.getLogger("gamepal.overlay")


class Backend(Protocol):
    def render(self, view: View | None) -> None: ...

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
    client = OverlayClient(http_url, ws_url, token, backend.render)
    worker = threading.Thread(target=client.run, name="overlay-client", daemon=True)
    worker.start()
    try:
        backend.run()
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
