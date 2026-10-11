"""Eventos que circulan por el bus interno.

Son inmutables y serializables a JSON para enviarlos tal cual a la GUI.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    topic: ClassVar[str] = "event"
    at: float = Field(default_factory=time.time)

    def to_message(self) -> dict[str, Any]:
        return {"topic": self.topic, "data": self.model_dump(mode="json")}


class Box(BaseModel):
    """Caja normalizada a [0, 1] respecto al frame. Solo se usa para relaciones relativas."""

    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


class TextLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float
    box: Box


class WindowSeen(Event):
    topic: ClassVar[str] = "capture.window"
    title: str | None
    process: str | None
    width: int
    height: int
    foreground: bool


class FrameCaptured(Event):
    topic: ClassVar[str] = "capture.frame"
    frame_id: int
    width: int
    height: int
    distance: int
    changed: bool


class ScreenRead(Event):
    topic: ClassVar[str] = "perception.text"
    frame_id: int
    backend: str
    elapsed_ms: float
    lines: list[TextLine]


class StatusChanged(Event):
    topic: ClassVar[str] = "status"
    component: Literal["capture", "ocr", "provider", "profile", "daemon"]
    state: Literal["idle", "starting", "running", "paused", "waiting", "error", "ready"]
    detail: str = ""


class LogMessage(Event):
    topic: ClassVar[str] = "log"
    level: Literal["debug", "info", "warning", "error"]
    message: str
