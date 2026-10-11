"""Conexión del overlay con el daemon. Corre en un hilo y avisa al backend cuando cambia la vista."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable

import httpx

from gamepal.overlay.model import OverlayModel, Rect, View

log = logging.getLogger(__name__)

TICK_S = 0.25


class OverlayClient:
    def __init__(
        self,
        http_url: str,
        ws_url: str,
        token: str,
        render: Callable[[View | None], None],
        screen: Rect | None = None,
    ) -> None:
        self._http = http_url.rstrip("/")
        self._ws = ws_url
        self._token = token
        self._render = render
        self.model = OverlayModel()
        if screen is not None:
            self.model.screen = screen
        self._last: View | None = None
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _configure(self) -> None:
        response = httpx.get(
            f"{self._http}/api/config",
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=5,
            trust_env=False,
        )
        response.raise_for_status()
        self.model.configure(response.json())

    def _push(self) -> None:
        view = self.model.view()
        if view != self._last:
            self._last = view
            self._render(view)

    def run(self) -> None:
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect

        delay = 0.5
        while not self._stop.is_set():
            try:
                self._configure()
                with connect(self._ws, open_timeout=5, max_size=1 << 20) as socket:
                    socket.send(json.dumps({"token": self._token}))
                    delay = 0.5
                    while not self._stop.is_set():
                        try:
                            raw = socket.recv(timeout=TICK_S)
                        except TimeoutError:
                            raw = None
                        if raw is not None:
                            self.model.handle(json.loads(raw))
                        self._push()
            except (OSError, ConnectionClosed, httpx.HTTPError, ValueError) as exc:
                log.debug("Overlay desconectado: %s", exc)
                self._render(None)
                self._last = None
                time.sleep(delay)
                delay = min(delay * 2, 5)
