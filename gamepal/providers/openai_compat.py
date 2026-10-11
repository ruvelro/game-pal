"""Proveedor para cualquier servidor compatible con la API de OpenAI que corra en este equipo.

Funciona con Ollama, LM Studio, llama-server (llama.cpp), vLLM o MLX. Solo acepta loopback.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from gamepal.providers.base import Capabilities, ChatResult, Message, ProviderError, Usage
from gamepal.security import validate_local_endpoint

MAX_RESPONSE_CHARS = 200_000


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


class LocalOpenAIProvider:
    name = "local-openai"

    def __init__(
        self,
        base_url: str,
        model: str = "",
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = validate_local_endpoint(base_url)
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # Sin proxies ni redirecciones: el tráfico local no debe acabar fuera del equipo.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=5.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _model(self, model: str | None) -> str:
        chosen = model or self.model
        if not chosen:
            raise ProviderError("No hay modelo seleccionado para el proveedor local")
        return chosen

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"No se pudo contactar con {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"El servidor local respondió {response.status_code}: {_error_detail(response)}")
        data = response.json()
        if not isinstance(data, dict):
            raise ProviderError("Respuesta inesperada del servidor local")
        return data

    async def capabilities(self) -> Capabilities:
        try:
            response = await self._client.get("/models")
        except httpx.HTTPError as exc:
            raise ProviderError(f"No se pudo contactar con {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"El servidor local respondió {response.status_code}: {_error_detail(response)}")
        data = response.json()
        items = data.get("data", []) if isinstance(data, dict) else []
        models = tuple(sorted(str(item["id"]) for item in items if isinstance(item, dict) and "id" in item))
        return Capabilities(models=models, local=True)

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        chosen = self._model(model)
        payload: dict[str, Any] = {
            "model": chosen,
            "messages": [{"role": m.role, "content": _content(m)} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
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
        usage = data.get("usage") or {}
        return ChatResult(
            text=str(text)[:MAX_RESPONSE_CHARS],
            model=str(data.get("model", chosen)),
            usage=Usage(usage.get("prompt_tokens"), usage.get("completion_tokens")),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self._model(model),
            "messages": [{"role": m.role, "content": _content(m)} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        received = 0
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise ProviderError(
                        f"El servidor local respondió {response.status_code}: {_error_detail(response)}"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {}).get("content")
                    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                        continue
                    if delta:
                        received += len(delta)
                        if received > MAX_RESPONSE_CHARS:
                            raise ProviderError("Respuesta demasiado larga")
                        yield delta
        except httpx.HTTPError as exc:
            raise ProviderError(f"Se cortó la conexión con {self.base_url}: {exc}") from exc

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        data = await self._post("/embeddings", {"model": self._model(model), "input": texts})
        items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
        return [list(map(float, item["embedding"])) for item in items]
