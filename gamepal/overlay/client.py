"""Conexión del overlay con el daemon. Corre en un hilo y avisa al backend cuando cambia la vista."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from gamepal.overlay.model import ChatView, OverlayModel, Rect, View

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
        render_chat: Callable[[ChatView | None], None] | None = None,
    ) -> None:
        self._http = http_url.rstrip("/")
        self._ws = ws_url
        self._token = token
        self._render = render
        self._render_chat = render_chat
        self.model = OverlayModel()
        if screen is not None:
            self.model.screen = screen
        self._last: View | None = None
        self._last_chat: ChatView | None = None
        self._lock = threading.Lock()
        self._connected_at = 0.0
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
        with self._lock:
            view = self.model.view()
            chat = self.model.chat_view() if self._render_chat is not None else None
        if view != self._last:
            self._last = view
            self._render(view)
        if self._render_chat is not None and chat != self._last_chat:
            self._last_chat = chat
            self._render_chat(chat)

    def submit(self, text: str) -> None:
        """Lo escrito en el chat: se pregunta como desde la GUI. Lo llama el hilo de la ventana; no lo bloquea."""
        text = text.strip()
        if text:
            threading.Thread(target=self._ask, args=(text[:4000],), name="overlay-ask", daemon=True).start()

    def _ask(self, text: str) -> None:
        try:
            response = httpx.post(
                f"{self._http}/api/assistant/ask",
                json={"text": text},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10,
                trust_env=False,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("No se pudo enviar la pregunta del chat: %s", exc)

    def close_chat(self) -> None:
        """Esc en el chat: se cierra aquí mismo, sin esperar al daemon."""
        with self._lock:
            self.model.chat_open = False
        self._push()

    def _handle(self, message: dict[str, Any]) -> None:
        # El bus reenvía el último evento de cada tipo al conectarse: una orden de abrir el chat de antes
        # de esta conexión no debe abrirlo al reiniciarse el overlay.
        if message.get("topic") == "overlay.chat":
            sent = (message.get("data") or {}).get("at")
            if isinstance(sent, int | float) and sent < self._connected_at:
                return
        with self._lock:
            self.model.handle(message)

    def run(self) -> None:
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect

        delay = 0.5
        while not self._stop.is_set():
            try:
                self._configure()
                with connect(self._ws, open_timeout=5, max_size=1 << 20) as socket:
                    socket.send(json.dumps({"token": self._token}))
                    self._connected_at = time.time()
                    delay = 0.5
                    while not self._stop.is_set():
                        try:
                            raw = socket.recv(timeout=TICK_S)
                        except TimeoutError:
                            raw = None
                        if raw is not None:
                            self._handle(json.loads(raw))
                        self._push()
            except (OSError, ConnectionClosed, httpx.HTTPError, ValueError) as exc:
                log.debug("Overlay desconectado: %s", exc)
                self._render(None)
                self._last = None
                if self._render_chat is not None:
                    self._render_chat(None)
                    self._last_chat = None
                time.sleep(delay)
                delay = min(delay * 2, 5)
