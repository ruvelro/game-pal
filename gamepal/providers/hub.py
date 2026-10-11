"""Enrutado de modelos por tarea, con respaldo y registro de actividad.

Cada tarea (razonar, decisiones rápidas, visión, resúmenes…) tiene un proveedor principal y, si se
quiere, uno de respaldo para cuando el principal falla. El jugador elige un preajuste en la GUI:
solo local, local con la nube de respaldo, nube para razonar y local para mirar, o nube para todo.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from gamepal.activity import ActivityStore, RequestRecord
from gamepal.bus import EventBus
from gamepal.config import AppConfig, Role
from gamepal.events import ActivityRecorded
from gamepal.keystore import SecretStore
from gamepal.providers.anthropic_provider import DEFAULT_FAST_MODEL, DEFAULT_MODEL, AnthropicProvider
from gamepal.providers.base import Capabilities, ChatResult, Message, ModelTraits, Provider, ProviderError, Usage
from gamepal.providers.openai_compat import LocalOpenAIProvider, OpenAIProvider, OpenRouterProvider

log = logging.getLogger(__name__)

ProviderName = str


def model_for(config: AppConfig, provider: ProviderName, role: Role) -> str:
    """El modelo que usa un proveedor para una tarea, según lo configurado."""
    if provider == "local":
        local = config.provider
        if role == "fast":
            return local.fast_model or local.model
        if role == "vision":
            return local.vision_model
        return local.model
    cloud = getattr(config.cloud, provider)
    if role == "fast":
        return cloud.fast_model or cloud.model or (DEFAULT_FAST_MODEL if provider == "anthropic" else "")
    if role == "vision":
        return cloud.vision_model or cloud.model or (DEFAULT_MODEL if provider == "anthropic" else "")
    return cloud.model or (DEFAULT_MODEL if provider == "anthropic" else "")


class ProviderHub:
    def __init__(
        self,
        bus: EventBus,
        *,
        config: Callable[[], AppConfig],
        secrets: SecretStore,
        activity: ActivityStore | None,
        factories: dict[str, Callable[[AppConfig, str | None], Provider]] | None = None,
    ) -> None:
        self._bus = bus
        self._config = config
        self.secrets = secrets
        self.activity = activity
        self._factories = factories or {
            "local": lambda cfg, _key: LocalOpenAIProvider(
                cfg.provider.base_url, cfg.provider.model, timeout=cfg.provider.timeout_seconds
            ),
            "openai": lambda cfg, key: OpenAIProvider(key or "", timeout=cfg.provider.timeout_seconds),
            "anthropic": lambda cfg, key: AnthropicProvider(key or "", timeout=max(cfg.provider.timeout_seconds, 60)),
            "openrouter": lambda cfg, key: OpenRouterProvider(key or "", timeout=cfg.provider.timeout_seconds),
        }
        self._clients: dict[str, Provider] = {}

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    async def reset(self) -> None:
        """Tras cambiar la configuración o una clave, los clientes se recrean al usarlos."""
        await self.aclose()

    def client(self, name: ProviderName) -> Provider:
        if name not in self._clients:
            factory = self._factories.get(name)
            if factory is None:
                raise ProviderError(f"Proveedor desconocido: {name}")
            key = None
            if name != "local":
                key = self.secrets.get(name)  # type: ignore[arg-type]
                if not key:
                    raise ProviderError(f"Falta la clave de {name}; añádela en la página Modelos")
            self._clients[name] = factory(self._config(), key)
        return self._clients[name]

    def available(self, name: ProviderName) -> bool:
        return name == "local" or bool(self.secrets.get(name))  # type: ignore[arg-type]

    def route(self, role: Role) -> RoutedProvider:
        return RoutedProvider(self, role)

    def record(self, record: RequestRecord) -> None:
        if self.activity is None:
            return
        try:
            stored = self.activity.add(record)
        except Exception:
            log.exception("No se pudo registrar la actividad")
            return
        self._bus.publish(
            ActivityRecorded(
                role=stored.role, provider=stored.provider, local=stored.local, model=stored.model, ok=stored.ok,
                ms=round(stored.ms), input_tokens=stored.input_tokens, output_tokens=stored.output_tokens,
                cost=stored.cost, fallback=stored.fallback,
            )
        )  # fmt: skip


class RoutedProvider:
    """Un proveedor con la misma interfaz que los demás, que elige destino y respaldo por tarea."""

    def __init__(self, hub: ProviderHub, role: Role) -> None:
        self._hub = hub
        self.role = role
        self.name = f"ruta:{role}"

    @property
    def local(self) -> bool:
        return self._targets()[0][0] == "local"

    @property
    def model(self) -> str:
        return self._targets()[0][1]

    def _targets(self, model: str | None = None) -> list[tuple[str, str]]:
        config = self._hub._config()
        route = getattr(config.routing, self.role)
        targets = [(route.provider, model or model_for(config, route.provider, self.role))]
        if route.fallback not in ("none", route.provider):
            # El respaldo usa su propio modelo para la tarea: el nombre del principal no le sirve.
            targets.append((route.fallback, model_for(config, route.fallback, self.role)))
        return targets

    async def aclose(self) -> None:
        return None

    async def capabilities(self) -> Capabilities:
        provider, _ = self._targets()[0]
        return await self._hub.client(provider).capabilities()

    async def traits(self) -> tuple[str, ModelTraits | None]:
        """Modelo principal de la tarea y si razona. None: el proveedor no lo sabe decir."""
        provider, model = self._targets()[0]
        client = self._hub.client(provider)
        probe = getattr(client, "traits", None)
        if probe is None or not model:
            return model, None
        return model, await probe(model)

    def _record(self, provider: str, model: str, ok: bool, started: float, usage: Usage, fallback: bool,
                error: str = "") -> None:  # fmt: skip
        self._hub.record(
            RequestRecord(
                role=self.role, provider=provider, local=provider == "local", model=model, ok=ok,
                ms=(time.perf_counter() - started) * 1000, input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens, cost=usage.cost, fallback=fallback, error=error,
            )
        )  # fmt: skip

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
        last: ProviderError | None = None
        for index, (provider, chosen) in enumerate(self._targets(model)):
            started = time.perf_counter()
            try:
                client = self._hub.client(provider)
                result = await client.chat(
                    messages, model=chosen or None, max_tokens=max_tokens, temperature=temperature,
                    json_schema=json_schema, think=think,
                )  # fmt: skip
            except ProviderError as exc:
                self._record(provider, chosen, False, started, Usage(), index > 0, str(exc))
                last = exc
                continue
            self._record(provider, result.model or chosen, True, started, result.usage, index > 0)
            return result
        raise last or ProviderError("No hay ningún proveedor disponible para esta tarea")

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        on_usage: Callable[[Usage], None] | None = None,
        think: int = 0,
    ) -> AsyncIterator[str]:
        last: ProviderError | None = None
        for index, (provider, chosen) in enumerate(self._targets(model)):
            started = time.perf_counter()
            usage = Usage()
            emitted = False

            def capture(value: Usage) -> None:
                nonlocal usage
                usage = value
                if on_usage is not None:
                    on_usage(value)

            try:
                client = self._hub.client(provider)
                async for delta in client.chat_stream(
                    messages, model=chosen or None, max_tokens=max_tokens, temperature=temperature, on_usage=capture,
                    think=think,
                ):  # fmt: skip
                    emitted = True
                    yield delta
            except ProviderError as exc:
                self._record(provider, chosen, False, started, usage, index > 0, str(exc))
                last = exc
                if emitted:
                    raise  # ya se mostró parte de la respuesta: no se mezcla con otra
                continue
            self._record(provider, chosen, True, started, usage, index > 0)
            return
        raise last or ProviderError("No hay ningún proveedor disponible para esta tarea")

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        provider, chosen = self._targets(model)[0]
        return await self._hub.client(provider).embed(texts, model=chosen or None)
