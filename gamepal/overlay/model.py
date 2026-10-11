"""Lógica del overlay, independiente de cómo se dibuje: qué se muestra, dónde y hasta cuándo."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

MARGIN = 18
MAX_BODY_CHARS = 700

STATUS_TEXT = {
    "es": {"armed": "Te escucho…", "recording": "Grabando…", "transcribing": "Entendiendo…", "thinking": "Pensando…"},
    "en": {"armed": "Listening…", "recording": "Recording…", "transcribing": "Transcribing…", "thinking": "Thinking…"},
}


@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class View:
    """Lo que hay que dibujar. Coordenadas con origen arriba a la izquierda, como las de la captura."""

    title: str
    body: str
    status: str
    x: int
    y: int
    width: int
    anchor_bottom: bool
    opacity: float
    font_scale: float
    error: bool = False


@dataclass
class Card:
    id: str
    title: str
    text: str = ""
    done: bool = False
    error: str = ""
    done_at: float = 0.0


@dataclass
class OverlayModel:
    corner: str = "top-right"
    width: int = 420
    opacity: float = 0.92
    font_scale: float = 1.0
    linger: float = 12.0
    language: str = "es"
    game: Rect | None = None
    screen: Rect = field(default_factory=lambda: Rect(0, 0, 1920, 1080))
    card: Card | None = None
    voice: str = "idle"

    def configure(self, config: dict[str, Any]) -> None:
        overlay = config.get("output", {}).get("overlay", {})
        self.corner = overlay.get("corner", self.corner)
        self.width = int(overlay.get("width", self.width))
        self.opacity = float(overlay.get("opacity", self.opacity))
        self.font_scale = float(overlay.get("font_scale", self.font_scale))
        self.linger = float(overlay.get("linger_seconds", self.linger))
        self.language = "es" if str(config.get("output_language", "es")).startswith("es") else "en"

    def handle(self, message: dict[str, Any], now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        topic, data = message.get("topic"), message.get("data") or {}
        if topic == "capture.window" and data.get("width"):
            self.game = Rect(int(data.get("left", 0)), int(data.get("top", 0)), int(data["width"]), int(data["height"]))
        elif topic == "voice.state":
            self.voice = str(data.get("state", "idle"))
        elif topic == "assistant.answer" and "overlay" in (data.get("outputs") or []):
            if self.card is None or self.card.id != data.get("id"):
                self.card = Card(str(data.get("id")), str(data.get("title", "")))
            card = self.card
            card.text = str(data.get("text") or card.text)
            card.error = "" if data.get("error") == "cancelada" else str(data.get("error") or "")
            if data.get("done"):
                card.done, card.done_at = True, now
                if data.get("error") == "cancelada":
                    self.card = None
        elif topic == "voice.stopped" and self.card is not None:
            self.card = None

    def view(self, now: float | None = None) -> View | None:
        now = time.monotonic() if now is None else now
        card = self.card
        if card is not None and card.done and now - card.done_at > self.linger:
            self.card = card = None
        status = STATUS_TEXT[self.language].get(self.voice, "")
        if card is not None and not card.text and not card.error and not card.done:
            status = STATUS_TEXT[self.language]["thinking"]
        if card is None and not status:
            return None
        body = card.error if card is not None and card.error else (card.text if card else "")
        if len(body) > MAX_BODY_CHARS:
            # Mientras llega se muestra el final; las respuestas largas no caben sobre el juego.
            streaming = card is not None and not card.done
            body = ("…" + body[-MAX_BODY_CHARS:]) if streaming else body[:MAX_BODY_CHARS] + "…"
        area = self.game or self.screen
        width = min(self.width, max(200, area.width - 2 * MARGIN))
        right = self.corner.endswith("right")
        bottom = self.corner.startswith("bottom")
        x = area.left + area.width - width - MARGIN if right else area.left + MARGIN
        y = area.top + area.height - MARGIN if bottom else area.top + MARGIN
        return View(
            title=(card.title if card else "")[:90],
            body=body.strip(),
            status=status,
            x=x,
            y=y,
            width=width,
            anchor_bottom=bottom,
            opacity=self.opacity,
            font_scale=self.font_scale,
            error=bool(card and card.error),
        )
