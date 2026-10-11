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


@dataclass(frozen=True, slots=True)
class Capabilities:
    models: tuple[str, ...]
    vision: bool | None = None  # None: desconocido hasta probarlo
    streaming: bool = True
    embeddings: bool | None = None
    local: bool = True


class Provider(Protocol):
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
    ) -> ChatResult: ...

    def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        on_usage: Callable[[Usage], None] | None = None,
    ) -> AsyncIterator[str]: ...

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]: ...

    async def aclose(self) -> None: ...
