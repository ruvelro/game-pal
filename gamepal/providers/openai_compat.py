"""Proveedores con la API de OpenAI: el servidor local del jugador, OpenAI y OpenRouter.

- Local (Ollama, LM Studio, llama.cpp, vLLM, MLX): solo direcciones de este equipo, sin proxies.
- Remotos: solo HTTPS, con la clave en la cabecera y nunca en la URL.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from gamepal.providers.base import Capabilities, ChatResult, Message, ProviderError, Usage
from gamepal.security import validate_local_endpoint, validate_remote_endpoint

MAX_RESPONSE_CHARS = 200_000
OPENAI_URL = "https://api.openai.com/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1"

UsageSink = Callable[[Usage], None]


def _content(message: Message) -> str | list[dict[str, Any]]:
    if not message.images:
        return message.text
    parts: list[dict[str, Any]] = [{"type": "text", "text": message.text}]
    parts.extend({"type": "image_url", "image_url": {"url": image.data_url}} for image in message.images)
    return parts


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message", error))[:300]
        if error:
            return str(error)[:300]
    return str(body)[:300]


def _usage(data: Any) -> Usage:
    if not isinstance(data, dict):
        return Usage()
    cost = data.get("cost")
    return Usage(
        data.get("prompt_tokens"),
        data.get("completion_tokens"),
        float(cost) if isinstance(cost, int | float) else None,
    )


class OpenAICompatibleProvider:
    """Base común. Las subclases fijan el destino y las particularidades de cada servicio."""

    name = "openai-compatible"
    local = False
    # OpenAI usa max_completion_tokens y sus modelos de razonamiento no aceptan temperature.
    max_tokens_field = "max_tokens"
    send_temperature = True

    def __init__(
        self,
        base_url: str,
        model: str = "",
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = self._validate(base_url)
        self.model = model
        auth = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={**auth, **(headers or {})},
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=False,
            trust_env=not self.local,  # el tráfico local nunca pasa por un proxy
            transport=transport,
        )

    @staticmethod
    def _validate(url: str) -> str:
        return validate_remote_endpoint(url)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _model(self, model: str | None) -> str:
        chosen = model or self.model
        if not chosen:
            raise ProviderError(f"No hay modelo seleccionado para {self.name}")
        return chosen

    def _payload(
        self, messages: list[Message], model: str | None, max_tokens: int, temperature: float
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model(model),
            "messages": [{"role": m.role, "content": _content(m)} for m in messages],
            self.max_tokens_field: max_tokens,
        }
        if self.send_temperature:
            payload["temperature"] = temperature
        return payload

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"No se pudo contactar con {self.name}: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"{self.name} respondió {response.status_code}: {_error_detail(response)}")
        data = response.json()
        if not isinstance(data, dict):
            raise ProviderError(f"Respuesta inesperada de {self.name}")
        return data

    async def capabilities(self) -> Capabilities:
        try:
            response = await self._client.get("/models")
        except httpx.HTTPError as exc:
            raise ProviderError(f"No se pudo contactar con {self.name}: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"{self.name} respondió {response.status_code}: {_error_detail(response)}")
        data = response.json()
        items = data.get("data", []) if isinstance(data, dict) else []
        models = tuple(sorted(str(item["id"]) for item in items if isinstance(item, dict) and "id" in item))
        return Capabilities(models=models, local=self.local)

    def _extra(self) -> dict[str, Any]:
        return {}

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        payload = {**self._payload(messages, model, max_tokens, temperature), "stream": False, **self._extra()}
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": json_schema},
            }
        started = time.perf_counter()
        data = await self._post("/chat/completions", payload)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("La respuesta no contiene texto") from exc
        return ChatResult(
            text=str(text)[:MAX_RESPONSE_CHARS],
            model=str(data.get("model", payload["model"])),
            usage=_usage(data.get("usage")),
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
        payload = {
            **self._payload(messages, model, max_tokens, temperature),
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._extra(),
        }
        received = 0
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise ProviderError(f"{self.name} respondió {response.status_code}: {_error_detail(response)}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if on_usage is not None and isinstance(chunk, dict) and chunk.get("usage"):
                        on_usage(_usage(chunk["usage"]))
                    try:
                        delta = chunk["choices"][0].get("delta", {}).get("content")
                    except (KeyError, IndexError, TypeError, AttributeError):
                        continue
                    if delta:
                        received += len(delta)
                        if received > MAX_RESPONSE_CHARS:
                            raise ProviderError("Respuesta demasiado larga")
                        yield delta
        except httpx.HTTPError as exc:
            raise ProviderError(f"Se cortó la conexión con {self.name}: {exc}") from exc

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        data = await self._post("/embeddings", {"model": self._model(model), "input": texts})
        items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
        return [list(map(float, item["embedding"])) for item in items]


class LocalOpenAIProvider(OpenAICompatibleProvider):
    """El servidor de modelos del jugador, en este mismo equipo."""

    name = "local"
    local = True

    @staticmethod
    def _validate(url: str) -> str:
        return validate_local_endpoint(url)


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    max_tokens_field = "max_completion_tokens"
    send_temperature = False

    def __init__(self, api_key: str, model: str = "", **kwargs: Any) -> None:
        super().__init__(OPENAI_URL, model, api_key=api_key, **kwargs)


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"

    def __init__(self, api_key: str, model: str = "", **kwargs: Any) -> None:
        # Cabeceras con las que OpenRouter identifica la aplicación (opcionales, sin datos del jugador).
        headers = {"HTTP-Referer": "https://github.com/ruvelro/game-pal", "X-Title": "game-pal"}
        super().__init__(OPENROUTER_URL, model, api_key=api_key, headers=headers, **kwargs)

    def _extra(self) -> dict[str, Any]:
        # Pide a OpenRouter el coste real de cada petición.
        return {"usage": {"include": True}}
