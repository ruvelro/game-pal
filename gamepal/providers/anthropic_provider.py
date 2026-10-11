"""Proveedor de Claude con el SDK oficial de Anthropic.

Notas de los modelos actuales:
- En Claude Opus 5.5 el razonamiento va siempre activo y no se admite `temperature`; la profundidad
  se controla con `output_config.effort`. Las respuestas cortas de un juego van con esfuerzo bajo.
- El razonamiento consume `max_tokens`, así que se deja margen aunque la respuesta sea breve.
- Con los modelos que lo admiten se activa el respaldo del servidor ante rechazos (`fallbacks: "default"`):
  si un filtro de seguridad declina la petición, la API la repite con otro modelo adecuado.
"""

from __future__ import annotations

import base64
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from gamepal.providers.base import Capabilities, ChatResult, Message, ProviderError, Usage

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_FAST_MODEL = "claude-haiku-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Modelos que admiten el respaldo «default» del servidor.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"})
MIN_MAX_TOKENS = 2048

UsageSink = Callable[[Usage], None]


def _effort(max_tokens: int) -> str:
    # Respuestas cortas y tareas rápidas: esfuerzo bajo; respuestas razonadas: medio.
    return "low" if max_tokens <= 200 else "medium"


def _blocks(message: Message) -> str | list[dict[str, Any]]:
    if not message.images:
        return message.text
    blocks: list[dict[str, Any]] = []
    for image in message.images:
        header, _, data = image.data_url.partition(",")
        media_type = header.removeprefix("data:").split(";")[0] or "image/jpeg"
        base64.b64decode(data, validate=True)  # solo imágenes bien formadas
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    blocks.append({"type": "text", "text": message.text})
    return blocks


def _split(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """La API de Claude lleva el sistema aparte y no admite dos turnos seguidos del mismo rol."""
    system = "\n\n".join(m.text for m in messages if m.role == "system")
    turns: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        turns.append({"role": message.role, "content": _blocks(message)})
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": "(inicio)"})
    return system, turns


class AnthropicProvider:
    name = "anthropic"
    local = False

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *, timeout: float = 120.0, client: Any = None) -> None:
        if client is None:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self._client = client
        self.model = model or DEFAULT_MODEL

    async def aclose(self) -> None:
        await self._client.close()

    def _params(self, messages: list[Message], model: str | None, max_tokens: int) -> dict[str, Any]:
        chosen = model or self.model
        system, turns = _split(messages)
        params: dict[str, Any] = {
            "model": chosen,
            "max_tokens": max(max_tokens * 4, MIN_MAX_TOKENS),
            "messages": turns,
            "output_config": {"effort": _effort(max_tokens)},
        }
        if system:
            params["system"] = system
        if chosen in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    @staticmethod
    def _error(exc: Exception) -> ProviderError:
        import anthropic

        if isinstance(exc, anthropic.AuthenticationError):
            return ProviderError("Anthropic rechazó la clave de API")
        if isinstance(exc, anthropic.RateLimitError):
            return ProviderError("Anthropic: límite de peticiones alcanzado; espera un momento")
        if isinstance(exc, anthropic.APIStatusError):
            return ProviderError(f"Anthropic respondió {exc.status_code}: {exc.message[:200]}")
        if isinstance(exc, anthropic.APIConnectionError):
            return ProviderError("No se pudo contactar con Anthropic")
        return ProviderError(f"Error de Anthropic: {exc}")

    @staticmethod
    def _usage(message: Any) -> Usage:
        usage = getattr(message, "usage", None)
        if usage is None:
            return Usage()
        return Usage(getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))

    @staticmethod
    def _text(message: Any) -> str:
        if getattr(message, "stop_reason", None) == "refusal":
            raise ProviderError("Claude ha declinado responder a esta petición")
        return "".join(block.text for block in message.content if getattr(block, "type", None) == "text")

    async def capabilities(self) -> Capabilities:
        try:
            page = await self._client.models.list(limit=100)
        except Exception as exc:
            raise self._error(exc) from exc
        models = tuple(sorted(model.id for model in page.data))
        return Capabilities(models=models, vision=True, embeddings=False, local=False)

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        params = self._params(messages, model, max_tokens)
        if json_schema is not None:
            params["output_config"] = {
                **params["output_config"],
                "format": {"type": "json_schema", "schema": json_schema},
            }
        started = time.perf_counter()
        try:
            async with self._client.beta.messages.stream(**params) as stream:
                message = await stream.get_final_message()
        except ProviderError:
            raise
        except Exception as exc:
            raise self._error(exc) from exc
        return ChatResult(
            text=self._text(message),
            model=str(getattr(message, "model", params["model"])),
            usage=self._usage(message),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        on_usage: UsageSink | None = None,
    ) -> AsyncIterator[str]:
        params = self._params(messages, model, max_tokens)
        try:
            async with self._client.beta.messages.stream(**params) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
        except ProviderError:
            raise
        except Exception as exc:
            raise self._error(exc) from exc
        if on_usage is not None:
            on_usage(self._usage(final))
        if getattr(final, "stop_reason", None) == "refusal":
            raise ProviderError("Claude ha declinado responder a esta petición")

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        raise ProviderError("Anthropic no ofrece embeddings; usa el modelo local o el servidor de modelos")
