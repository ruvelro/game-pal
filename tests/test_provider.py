import json

import httpx
import pytest

from gamepal.providers import ImagePart, LocalOpenAIProvider, Message, ProviderError
from gamepal.security import UnsafeUrlError
from tests.conftest import pattern


def _provider(handler: httpx.MockTransport, model: str = "qwen") -> LocalOpenAIProvider:
    return LocalOpenAIProvider("http://127.0.0.1:11434/v1", model, transport=handler)


async def test_lists_models() -> None:
    transport = httpx.MockTransport(
        lambda r: (
            httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})
            if r.url.path == "/v1/models"
            else httpx.Response(404)
        )
    )
    caps = await _provider(transport).capabilities()
    assert caps.models == ("a", "b")


async def test_chat_sends_images_and_schema() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "qwen",
                "choices": [{"message": {"content": "Coge la espada"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    image = ImagePart.from_rgb(pattern(1, 2000, 1000), max_width=640)
    result = await _provider(httpx.MockTransport(handler)).chat(
        [Message("user", "¿Qué cojo?", (image,))], json_schema={"type": "object"}
    )
    assert result.text == "Coge la espada"
    assert result.usage.input_tokens == 10
    content = seen["messages"][0]["content"]  # type: ignore[index]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert seen["response_format"]["json_schema"]["strict"] is True  # type: ignore[index]


async def test_stream_yields_deltas() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"Ho"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"la"}}]}\n\n'
        "data: no-json\n\n"
        "data: [DONE]\n\n"
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=body))
    chunks = [c async for c in _provider(transport).chat_stream([Message("user", "hola")])]
    assert "".join(chunks) == "Hola"


async def test_errors_are_readable() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json={"error": {"message": "model not found"}}))
    with pytest.raises(ProviderError, match="model not found"):
        await _provider(transport).chat([Message("user", "hola")])


async def test_requires_model() -> None:
    with pytest.raises(ProviderError, match="modelo"):
        await _provider(httpx.MockTransport(lambda r: httpx.Response(200)), model="").chat([Message("user", "x")])


def test_rejects_remote_hosts() -> None:
    with pytest.raises(UnsafeUrlError):
        LocalOpenAIProvider("http://10.0.0.5:11434/v1")
