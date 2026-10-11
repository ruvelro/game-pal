"""Interfaz común de proveedores de modelos."""

from __future__ import annotations

import base64
import io
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from PIL import Image

from gamepal.capture.base import RGBImage

Role = Literal["system", "user", "assistant"]


# Mensajes propios de game-pal cuando el modelo no llega a responder (explain.py los traduce).
OUT_OF_ROOM = "el modelo gastó todo el margen pensando y no llegó a responder"
EMPTY_ANSWER = "el modelo no devolvió ningún texto"


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ImagePart:
    data_url: str

    @classmethod
    def from_rgb(cls, image: RGBImage, max_width: int = 1280, quality: int = 85) -> ImagePart:
        picture = Image.fromarray(image, "RGB")
        if picture.width > max_width:
            picture = picture.resize((max_width, round(picture.height * max_width / picture.width)))
        buffer = io.BytesIO()
        picture.save(buffer, "JPEG", quality=quality)
        return cls("data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"))


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    text: str
    images: tuple[ImagePart, ...] = ()


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Coste en dólares cuando el servicio lo informa (OpenRouter); si no, se estima aparte.
    cost: float | None = None


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    elapsed_ms: float = 0.0


@dataclass(slots=True)
class ModelTraits:
    """Lo que se sabe de cómo razona un modelo. None: aún no se sabe."""

    reasons: bool | None = None
    # Si el servidor deja apagar el razonamiento de ese modelo.
    can_disable: bool | None = None


@dataclass(frozen=True, slots=True)
class Capabilities:
    models: tuple[str, ...]
    vision: bool | None = None  # None: desconocido hasta probarlo
    streaming: bool = True
    embeddings: bool | None = None
    local: bool = True


class Provider(Protocol):
    """Interfaz de un proveedor.

    `max_tokens` es el largo de la respuesta. `think` son los tokens que el modelo puede usar para pensar
    antes de responder, aparte de la respuesta; 0 pide que no piense (si el servidor lo permite).
    """

    name: str

    @property
    def local(self) -> bool:
        """True si el proveedor corre en este equipo."""
        ...

    async def capabilities(self) -> Capabilities: ...

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        json_schema: dict[str, Any] | None = None,
        think: int = 0,
    ) -> ChatResult: ...

    def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        on_usage: Callable[[Usage], None] | None = None,
        think: int = 0,
    ) -> AsyncIterator[str]: ...

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]: ...

    async def aclose(self) -> None: ...
