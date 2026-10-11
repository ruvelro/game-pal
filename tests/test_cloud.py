import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from gamepal.activity import ActivityStore, RequestRecord, estimate_cost
from gamepal.bus import EventBus
from gamepal.config import AppConfig
from gamepal.events import ActivityRecorded
from gamepal.keystore import MemorySecretStore
from gamepal.providers import ImagePart, Message, ProviderError
from gamepal.providers.anthropic_provider import AnthropicProvider
from gamepal.providers.base import Capabilities, ChatResult, Usage
from gamepal.providers.hub import ProviderHub, model_for
from gamepal.providers.openai_compat import OpenAIProvider, OpenRouterProvider
from tests.conftest import pattern

# Proveedores remotos compatibles con OpenAI ---------------------------------------------------


async def test_openai_uses_completion_tokens_and_no_temperature() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "auth": request.headers.get("authorization"),
                     **json.loads(request.content)})  # fmt: skip
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 3}})

    provider = OpenAIProvider("sk-test", "gpt-x", transport=httpx.MockTransport(handler))
    await provider.chat([Message("user", "hola")], max_tokens=50)
    assert seen[0]["url"].startswith("https://api.openai.com/v1/")
    assert seen[0]["auth"] == "Bearer sk-test"
    assert seen[0]["max_completion_tokens"] == 50
    assert "temperature" not in seen[0]
    assert "max_tokens" not in seen[0]


async def test_openrouter_reports_cost_while_streaming() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"Hola"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2,"cost":0.0004}}\n\n'
        "data: [DONE]\n\n"
    )
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=body)

    provider = OpenRouterProvider("sk-or", "m", transport=httpx.MockTransport(handler))
    usages: list[Usage] = []
    text = "".join([d async for d in provider.chat_stream([Message("user", "x")], on_usage=usages.append)])
    assert text == "Hola"
    assert usages == [Usage(10, 2, 0.0004)]
    assert sent[0]["usage"] == {"include": True}
    assert sent[0]["stream_options"] == {"include_usage": True}


# Claude -------------------------------------------------------------------------------------


class FakeStream:
    def __init__(self, texts: list[str], stop: str = "end_turn") -> None:
        self._texts = texts
        self._stop = stop

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    @property
    def text_stream(self) -> Any:
        async def gen() -> Any:
            for t in self._texts:
                yield t

        return gen()

    async def get_final_message(self) -> Any:
        return SimpleNamespace(
            model="claude-opus-5-5",
            stop_reason=self._stop,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text="".join(self._texts))],
        )  # fmt: skip


class FakeAnthropic:
    def __init__(self, texts: list[str], stop: str = "end_turn") -> None:
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self._texts, self._stop = texts, stop

    def _stream(self, **params: Any) -> FakeStream:
        self.calls.append(params)
        return FakeStream(self._texts, self._stop)

    async def close(self) -> None:
        return None


async def test_anthropic_request_shape() -> None:
    fake = FakeAnthropic(["Coge ", "el rayo."])
    provider = AnthropicProvider("k", client=fake)
    image = ImagePart.from_rgb(pattern(1), max_width=64)
    messages = [Message("system", "Eres útil."), Message("user", "¿Qué cojo?", (image,))]
    usages: list[Usage] = []
    text = "".join([d async for d in provider.chat_stream(messages, max_tokens=500, on_usage=usages.append)])
    assert text == "Coge el rayo."
    params = fake.calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["system"] == "Eres útil."
    assert "temperature" not in params
    assert "thinking" not in params
    assert params["output_config"] == {"effort": "medium"}
    assert params["max_tokens"] >= 2048  # margen para el razonamiento
    assert params["fallbacks"] == "default"
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    blocks = params["messages"][0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/jpeg"
    assert usages == [Usage(100, 20)]


async def test_anthropic_structured_output_and_refusal() -> None:
    fake = FakeAnthropic(['{"route": "quick"}'])
    provider = AnthropicProvider("k", model="claude-haiku-5-5", client=fake)
    result = await provider.chat([Message("user", "gracias")], max_tokens=20, json_schema={"type": "object"})
    assert result.text == '{"route": "quick"}'
    params = fake.calls[0]
    assert params["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert "fallbacks" not in params  # Haiku no tiene respaldo del servidor
    refused = AnthropicProvider("k", client=FakeAnthropic(["..."], stop="refusal"))
    with pytest.raises(ProviderError, match="declinado"):
        await refused.chat([Message("user", "x")])


# Enrutado -----------------------------------------------------------------------------------


class Stub:
    local = False

    def __init__(self, name: str, fail: bool = False, partial: bool = False) -> None:
        self.name, self.fail, self.partial = name, fail, partial
        self.models: list[str | None] = []

    async def capabilities(self) -> Capabilities:
        return Capabilities(models=(self.name,))

    async def chat(self, messages: Any, *, model: str | None = None, **kw: Any) -> ChatResult:
        self.models.append(model)
        if self.fail:
            raise ProviderError(f"{self.name} caído")
        return ChatResult("ok", model or self.name, Usage(10, 5))

    async def chat_stream(self, messages: Any, *, model: str | None = None, on_usage: Any = None, **kw: Any) -> Any:
        self.models.append(model)
        if self.partial:
            yield "medio"
            raise ProviderError("cortado")
        if self.fail:
            raise ProviderError(f"{self.name} caído")
        yield f"desde {self.name}"
        if on_usage:
            on_usage(Usage(1000, 100))

    async def embed(self, *a: Any, **k: Any) -> list[list[float]]:
        return []

    async def aclose(self) -> None:
        return None


def _factory(stub: Stub) -> Any:
    return lambda cfg, key: stub


def _hub(tmp_path: Path, config: AppConfig, stubs: dict[str, Stub], keys: tuple[str, ...] = ()) -> ProviderHub:
    secrets = MemorySecretStore()
    for key in keys:
        secrets.set(key, "k")  # type: ignore[arg-type]
    return ProviderHub(
        EventBus(), config=lambda: config, secrets=secrets, activity=ActivityStore(tmp_path / "a.sqlite"),
        factories={name: _factory(stub) for name, stub in stubs.items()},
    )  # fmt: skip


async def test_falls_back_when_primary_fails(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {"provider": {"model": "qwen"}, "routing": {"reasoning": {"provider": "local", "fallback": "anthropic"}}}
    )
    stubs = {"local": Stub("local", fail=True), "anthropic": Stub("anthropic")}
    hub = _hub(tmp_path, config, stubs, keys=("anthropic",))
    async with hub._bus.subscribe({"activity"}) as sub:
        text = "".join([d async for d in hub.route("reasoning").chat_stream([Message("user", "x")])])
        first, second = await sub.get(), await sub.get()
    assert text == "desde anthropic"
    assert stubs["local"].models == ["qwen"]
    assert stubs["anthropic"].models == ["claude-opus-5-5"]  # el respaldo usa su propio modelo
    assert isinstance(first, ActivityRecorded)
    assert isinstance(second, ActivityRecorded)
    assert (first.provider, first.ok, second.provider, second.ok, second.fallback) == (
        "local",
        False,
        "anthropic",
        True,
        True,
    )
    assert second.cost == pytest.approx((1000 * 4 + 100 * 20) / 1e6)


async def test_no_fallback_after_partial_answer(tmp_path: Path) -> None:
    config = AppConfig.model_validate({"routing": {"reasoning": {"provider": "local", "fallback": "openai"}}})
    stubs = {"local": Stub("local", partial=True), "openai": Stub("openai")}
    hub = _hub(tmp_path, config, stubs, keys=("openai",))
    chunks: list[str] = []

    async def consume() -> None:
        async for delta in hub.route("reasoning").chat_stream([Message("user", "x")]):
            chunks.append(delta)

    with pytest.raises(ProviderError, match="cortado"):
        await consume()
    assert chunks == ["medio"]
    assert stubs["openai"].models == []


async def test_missing_key_is_a_clear_error(tmp_path: Path) -> None:
    config = AppConfig.model_validate({"routing": {"reasoning": {"provider": "openai"}}})
    hub = _hub(tmp_path, config, {"openai": Stub("openai")})
    with pytest.raises(ProviderError, match="Falta la clave de openai"):
        await hub.route("reasoning").chat([Message("user", "x")])


def test_models_per_role() -> None:
    config = AppConfig.model_validate(
        {"provider": {"model": "qwen", "fast_model": "tiny"}, "cloud": {"openai": {"model": "big"}}}
    )
    assert model_for(config, "local", "fast") == "tiny"
    assert model_for(config, "local", "vision") == ""
    assert model_for(config, "openai", "fast") == "big"
    assert model_for(config, "anthropic", "reasoning") == "claude-opus-5-5"
    assert model_for(config, "anthropic", "fast") == "claude-haiku-5-5"


def test_activity_store_summary(tmp_path: Path) -> None:
    store = ActivityStore(tmp_path / "a.sqlite")
    store.add(RequestRecord("reasoning", "local", True, "qwen", True, 120))
    stored = store.add(RequestRecord("reasoning", "anthropic", False, "claude-haiku-5-5", True, 300, 1000, 1000))
    assert stored.cost == pytest.approx(estimate_cost("claude-haiku-5-5", 1000, 1000))
    summary = store.summary(0)
    assert (summary["total"], summary["local"], summary["cloud"]) == (2, 1, 1)
    assert summary["cost"] == pytest.approx(stored.cost)
    assert estimate_cost("modelo-desconocido", 1, 1) is None
