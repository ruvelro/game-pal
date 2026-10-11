"""Proveedores con la API de OpenAI: el servidor local del jugador, OpenAI y OpenRouter.

- Local (Ollama, LM Studio, llama.cpp, vLLM, MLX): solo direcciones de este equipo, sin proxies.
- Remotos: solo HTTPS, con la clave en la cabecera y nunca en la URL.

Modelos que razonan: lo que piensan llega aparte (`reasoning` o `reasoning_content`) o entre etiquetas
`<think>`, y cuenta en `max_tokens`. Por eso:
- Se averigua si el modelo razona: Ollama lo dice en /api/show; en los demás se nota en la primera respuesta.
- Si no debe pensar, se pide apagarlo cuando el servidor lo permite; si no, se le da margen.
- Si debe pensar, el margen se suma al límite solo cuando el modelo razona, para no alargar las respuestas
  de los que no lo hacen.
- Si aun así se queda sin margen y no responde nada, se repite una vez sin pensar (o con más margen) y,
  si tampoco, se avisa en lugar de devolver una respuesta vacía.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from gamepal.providers.base import OUT_OF_ROOM, Capabilities, ChatResult, Message, ModelTraits, ProviderError, Usage
from gamepal.security import validate_local_endpoint, validate_remote_endpoint

MAX_RESPONSE_CHARS = 200_000
OPENAI_URL = "https://api.openai.com/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1"

UsageSink = Callable[[Usage], None]

# Campos en los que los servidores mandan lo que piensa el modelo (Ollama y OpenRouter, llama.cpp y vLLM).
REASONING_FIELDS = ("reasoning", "reasoning_content")
# Margen para pensar cuando una tarea no debería, pero el servidor no deja apagarlo.
FORCED_THINK_TOKENS = 1024
_THINK_BLOCK = re.compile(r"^\s*<think>(.*?)(</think>\s*|$)", re.DOTALL)
# Lo que se sabe de cada modelo, por servidor. Dura lo que dure el proceso.
_TRAITS: dict[tuple[str, str], ModelTraits] = {}


def strip_think(text: str) -> tuple[str, bool]:
    """Quita el bloque <think>…</think> con el que algunos servidores empiezan la respuesta.

    Devuelve el texto y si el modelo pensó algo. Un bloque vacío (Qwen 3 con el razonamiento apagado)
    no cuenta como pensar.
    """
    match = _THINK_BLOCK.match(text)
    if match is None:
        return text, False
    return text[match.end() :], bool(match.group(1).strip())


class _ThinkFilter:
    """Separa en vivo el bloque <think> inicial del texto de la respuesta."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        # start: aún no se sabe; inside: pensando; after: tras </think>, sin texto aún; answer: respondiendo
        self._state = "start"
        self.thought = False

    def feed(self, text: str) -> str:
        if self._state == "answer":
            return text
        if self._state == "after":
            # Los saltos de línea que siguen a </think> no son parte de la respuesta.
            text = text.lstrip()
            if text:
                self._state = "answer"
            return text
        self._buffer += text
        if self._state == "start":
            head = self._buffer.lstrip()
            if not head or (len(head) < len(self.OPEN) and self.OPEN.startswith(head)):
                return ""
            if not head.startswith(self.OPEN):
                self._state = "answer"
                out, self._buffer = self._buffer, ""
                return out
            self._state = "inside"
            self._buffer = head[len(self.OPEN) :]
        end = self._buffer.find(self.CLOSE)
        # Solo cuenta como pensar si hay algo dentro (sin contar una etiqueta de cierre a medio llegar).
        inner = self._buffer[:end] if end >= 0 else self._buffer[: max(0, len(self._buffer) - len(self.CLOSE))]
        self.thought = self.thought or bool(inner.strip())
        if end < 0:
            # Se guarda solo lo justo para reconocer la etiqueta de cierre partida entre dos trozos.
            self._buffer = self._buffer[-len(self.CLOSE) :]
            return ""
        out, self._buffer = self._buffer[end + len(self.CLOSE) :].lstrip(), ""
        self._state = "answer" if out else "after"
        return out

    def flush(self) -> str:
        """Al terminar: lo que quedó esperando por si era el principio de <think>."""
        out = self._buffer if self._state == "start" else ""
        self._buffer = ""
        return out


@dataclass(slots=True)
class _Outcome:
    """Cómo terminó un intento: cuánto texto dio, si pensó y por qué paró."""

    chars: int = 0
    thought: bool = False
    finish: str | None = None
    rejected: bool = False  # el servidor no aceptó los parámetros para apagar el razonamiento


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


def _rejected(error: ProviderError) -> bool:
    """El servidor no acepta los parámetros para apagar el razonamiento (400 o 422 de validación)."""
    return bool(re.search(r"respondió (400|422)", str(error)))


class OpenAICompatibleProvider:
    """Base común. Las subclases fijan el destino y las particularidades de cada servicio."""

    name = "openai-compatible"
    local = False
    # OpenAI usa max_completion_tokens y sus modelos de razonamiento no aceptan temperature.
    max_tokens_field = "max_tokens"
    send_temperature = True
    # Parámetros con los que este servicio deja apagar el razonamiento. Vacío: no se puede.
    no_think: ClassVar[dict[str, Any]] = {}

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

    # Razonamiento --------------------------------------------------------------------------------

    def _known(self, model: str) -> ModelTraits:
        return _TRAITS.setdefault((self.base_url, model), self._guess(model))

    def _guess(self, model: str) -> ModelTraits:
        """Lo que se puede saber sin preguntar al servidor."""
        return ModelTraits(can_disable=None if self.no_think else False)

    async def _probe(self, model: str, traits: ModelTraits) -> None:
        """Pregunta al servidor si el modelo razona. Por defecto no se puede saber de antemano."""

    async def traits(self, model: str | None = None) -> ModelTraits:
        """Lo que se sabe del modelo: si razona y si se le puede apagar."""
        chosen = self._model(model)
        key = (self.base_url, chosen)
        if key not in _TRAITS:
            traits = self._known(chosen)
            await self._probe(chosen, traits)
        return _TRAITS[key]

    def _plan(self, traits: ModelTraits, max_tokens: int, think: int, allow_off: bool) -> tuple[int, dict[str, Any]]:
        """Límite de tokens y parámetros extra para un intento."""
        if think <= 0 and traits.reasons is not False and traits.can_disable is not False and allow_off:
            return max_tokens, dict(self.no_think)
        if not traits.reasons:
            return max_tokens, {}
        return max_tokens + (think if think > 0 else FORCED_THINK_TOKENS), {}

    def _learn(self, traits: ModelTraits, outcome: _Outcome, asked_off: bool) -> None:
        if outcome.thought:
            traits.reasons = True
            if asked_off:
                traits.can_disable = False  # se le pidió no pensar y pensó igual
        elif outcome.chars and traits.reasons is None and not asked_off:
            traits.reasons = False
        if outcome.rejected:
            traits.can_disable = False

    @staticmethod
    def _starved(outcome: _Outcome) -> bool:
        """Terminó sin responder nada porque se le acabó el sitio (casi siempre, pensando)."""
        return not outcome.chars and (outcome.thought or outcome.finish == "length")

    def _retry_think(self, traits: ModelTraits, think: int) -> int:
        """Margen para el segundo intento. Si podía pensar, ahora sin pensar (si se puede) o con el doble."""
        if think > 0 and traits.can_disable is not False and self.no_think:
            return 0
        return max(think, FORCED_THINK_TOKENS) * 2

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        json_schema: dict[str, Any] | None = None,
        think: int = 0,
    ) -> ChatResult:
        traits = await self.traits(model)
        started = time.perf_counter()
        allow_off = True
        for attempt in range(3):
            limit, extra = self._plan(traits, max_tokens, think, allow_off)
            payload = {
                **self._payload(messages, model, limit, temperature), "stream": False, **self._extra(), **extra
            }  # fmt: skip
            if json_schema is not None:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "strict": True, "schema": json_schema},
                }
            try:
                data = await self._post("/chat/completions", payload)
            except ProviderError as exc:
                if extra and _rejected(exc):
                    traits.can_disable, allow_off = False, False
                    continue
                raise
            try:
                choice = data["choices"][0]
                message = choice["message"]
                raw = str(message.get("content") or "")
            except (KeyError, IndexError, TypeError, AttributeError) as exc:
                raise ProviderError("La respuesta no contiene texto") from exc
            text, thought = strip_think(raw)
            text = text.strip()
            thought = thought or any(message.get(f) for f in REASONING_FIELDS)
            outcome = _Outcome(len(text), thought, choice.get("finish_reason"))
            self._learn(traits, outcome, bool(extra))
            if not self._starved(outcome) or attempt == 2:
                break
            think = self._retry_think(traits, think)
        if self._starved(outcome):
            raise ProviderError(f"{self.name}: {OUT_OF_ROOM}")
        return ChatResult(
            text=text[:MAX_RESPONSE_CHARS],
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
        think: int = 0,
    ) -> AsyncIterator[str]:
        traits = await self.traits(model)
        allow_off = True
        for attempt in range(3):
            limit, extra = self._plan(traits, max_tokens, think, allow_off)
            payload = {
                **self._payload(messages, model, limit, temperature),
                "stream": True,
                "stream_options": {"include_usage": True},
                **self._extra(),
                **extra,
            }
            outcome = _Outcome()
            async for delta in self._stream_once(payload, outcome, on_usage, bool(extra)):
                yield delta
            if outcome.rejected:
                traits.can_disable, allow_off = False, False
                continue
            self._learn(traits, outcome, bool(extra))
            if not self._starved(outcome):
                return
            if attempt == 2:
                break
            think = self._retry_think(traits, think)
        raise ProviderError(f"{self.name}: {OUT_OF_ROOM}")

    async def _stream_once(
        self, payload: dict[str, Any], outcome: _Outcome, on_usage: UsageSink | None, may_reject: bool
    ) -> AsyncIterator[str]:
        tags = _ThinkFilter()
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    await response.aread()
                    error = ProviderError(f"{self.name} respondió {response.status_code}: {_error_detail(response)}")
                    if may_reject and _rejected(error):
                        outcome.rejected = True
                        return
                    raise error
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if on_usage is not None and isinstance(chunk, dict) and chunk.get("usage"):
                        on_usage(_usage(chunk["usage"]))
                    try:
                        choice = chunk["choices"][0]
                        delta = choice.get("delta") or {}
                    except (KeyError, IndexError, TypeError, AttributeError):
                        continue
                    outcome.finish = choice.get("finish_reason") or outcome.finish
                    if any(delta.get(f) for f in REASONING_FIELDS):
                        outcome.thought = True
                    text = tags.feed(delta.get("content") or "")
                    outcome.thought = outcome.thought or tags.thought
                    if text:
                        outcome.chars += len(text)
                        if outcome.chars > MAX_RESPONSE_CHARS:
                            raise ProviderError("Respuesta demasiado larga")
                        yield text
        except httpx.HTTPError as exc:
            raise ProviderError(f"Se cortó la conexión con {self.name}: {exc}") from exc
        rest = tags.flush()
        if rest.strip():
            outcome.chars += len(rest)
            yield rest

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        data = await self._post("/embeddings", {"model": self._model(model), "input": texts})
        items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
        return [list(map(float, item["embedding"])) for item in items]


class LocalOpenAIProvider(OpenAICompatibleProvider):
    """El servidor de modelos del jugador, en este mismo equipo."""

    name = "local"
    local = True
    # Ollama lo apaga con reasoning_effort; llama.cpp, vLLM y MLX, con la plantilla del modelo.
    # Cada servidor ignora el parámetro del otro; si alguno los rechaza, se deja de enviar.
    no_think: ClassVar[dict[str, Any]] = {
        "reasoning_effort": "none",
        "chat_template_kwargs": {"enable_thinking": False},
    }

    @staticmethod
    def _validate(url: str) -> str:
        return validate_local_endpoint(url)

    async def _probe(self, model: str, traits: ModelTraits) -> None:
        """Ollama dice en /api/show si el modelo piensa. Otros servidores no responden ahí y no pasa nada."""
        root = self.base_url.removesuffix("/").removesuffix("/v1")
        try:
            response = await self._client.post(f"{root}/api/show", json={"model": model}, timeout=5.0)
            data = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return
        if not isinstance(data, dict) or not isinstance(data.get("capabilities"), list):
            return
        capabilities = data["capabilities"]
        traits.reasons = "thinking" in capabilities
        family = str((data.get("details") or {}).get("family", "")).lower()
        # gpt-oss siempre razona: Ollama solo deja elegir cuánto.
        traits.can_disable = traits.reasons and "gptoss" not in family.replace("-", "")


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    max_tokens_field = "max_completion_tokens"
    send_temperature = False
    _REASONING = re.compile(r"^(o\d|gpt-5)", re.IGNORECASE)

    def _guess(self, model: str) -> ModelTraits:
        # Los o1/o3/o4 y GPT-5 siempre razonan y el razonamiento cuenta en max_completion_tokens.
        return ModelTraits(reasons=bool(self._REASONING.match(model)) or None, can_disable=False)

    def __init__(self, api_key: str, model: str = "", **kwargs: Any) -> None:
        super().__init__(OPENAI_URL, model, api_key=api_key, **kwargs)


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    no_think: ClassVar[dict[str, Any]] = {"reasoning": {"enabled": False}}

    def __init__(self, api_key: str, model: str = "", **kwargs: Any) -> None:
        # Cabeceras con las que OpenRouter identifica la aplicación (opcionales, sin datos del jugador).
        headers = {"HTTP-Referer": "https://github.com/ruvelro/game-pal", "X-Title": "game-pal"}
        super().__init__(OPENROUTER_URL, model, api_key=api_key, headers=headers, **kwargs)

    def _extra(self) -> dict[str, Any]:
        # Pide a OpenRouter el coste real de cada petición.
        return {"usage": {"include": True}}
