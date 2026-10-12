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
    left: int = 0
    top: int = 0


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


class ScreenDetected(Event):
    """Pantalla reconocida (screen_id) o fin de la pantalla anterior (screen_id = None)."""

    topic: ClassVar[str] = "perception.screen"
    screen_id: str | None
    change: Literal["enter", "update", "leave"] = "enter"
    name: str = ""
    confidence: float = 0.0
    certain: bool = False
    complete: bool = True
    source: Literal["signals", "vision"] = "signals"
    data: dict[str, Any] = Field(default_factory=dict)
    frame_id: int = 0


class StatusChanged(Event):
    topic: ClassVar[str] = "status"
    component: Literal[
        "capture",
        "ocr",
        "provider",
        "profile",
        "daemon",
        "voice",
        "overlay",
        "hotkeys",
        "save",
        "memory",
        "knowledge",
        "runtime",
    ]
    state: Literal["idle", "starting", "running", "paused", "waiting", "error", "ready"]
    detail: str = ""
    # De 0 a 1 mientras se descarga algo; la GUI lo muestra como una barra.
    progress: float | None = None


class VoiceState(Event):
    """Qué está haciendo el micrófono, para la GUI y el overlay."""

    topic: ClassVar[str] = "voice.state"
    state: Literal["off", "idle", "armed", "recording", "transcribing", "error"]
    detail: str = ""


class QuestionAsked(Event):
    topic: ClassVar[str] = "assistant.question"
    id: str
    text: str
    source: Literal["voice", "text", "push_to_talk"]


class AnswerUpdate(Event):
    """Respuesta en curso. `delta` trae el texto nuevo; `done` marca el final."""

    topic: ClassVar[str] = "assistant.answer"
    id: str
    kind: Literal["question", "proactive"]
    title: str = ""
    delta: str = ""
    text: str = ""
    done: bool = False
    error: str = ""
    outputs: list[Literal["overlay", "text", "voice"]] = Field(default_factory=list)
    route: Literal["quick", "reasoned", "task"] = "reasoned"
    model: str = ""
    # Fuentes de conocimiento citadas como [n] en el texto.
    sources: list[dict[str, str]] = Field(default_factory=list)


class ChatToggled(Event):
    """Abrir o cerrar el chat sobre el juego. Sin `open`, lo alterna (el atajo de teclado)."""

    topic: ClassVar[str] = "overlay.chat"
    open: bool | None = None


class SpeakingChanged(Event):
    topic: ClassVar[str] = "voice.speaking"
    speaking: bool


class SpeechStopped(Event):
    topic: ClassVar[str] = "voice.stopped"
    reason: str = ""


class KnowledgeProgress(Event):
    topic: ClassVar[str] = "knowledge.progress"
    profile: str
    source_id: int
    location: str
    state: Literal["queued", "running", "ready", "error"]
    pages: int = 0
    message: str = ""


class EvidenceChanged(Event):
    """Estado de la partida tal y como lo conoce game-pal, con el origen de cada dato."""

    topic: ClassVar[str] = "evidence"
    snapshot: dict[str, Any]


class ActivityRecorded(Event):
    """Una petición a un modelo terminada. Nunca lleva el contenido, solo métricas."""

    topic: ClassVar[str] = "activity"
    role: str
    provider: str
    local: bool
    model: str
    ok: bool
    ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    fallback: bool = False


class DownloadProgress(Event):
    """Descarga del motor o de un modelo del catálogo."""

    topic: ClassVar[str] = "download"
    item: str
    label: str
    received: int = 0
    total: int = 0
    state: Literal["queued", "running", "verifying", "done", "error", "cancelled"] = "running"
    error: str = ""


class LogMessage(Event):
    topic: ClassVar[str] = "log"
    level: Literal["debug", "info", "warning", "error"]
    message: str
