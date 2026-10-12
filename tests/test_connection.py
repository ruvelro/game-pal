import httpx

from gamepal.connection import check_connection
from gamepal.providers import LocalOpenAIProvider

URL = "http://192.168.1.20:11434/v1"


def _server(handler: httpx.MockTransport) -> LocalOpenAIProvider:
    return LocalOpenAIProvider(URL, transport=handler)


def _models(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": name} for name in names]})


async def test_a_working_server_passes_every_step() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return _models("qwen3:8b", "gemma4:e4b")
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"model": "qwen3:8b", "choices": [{"message": {"content": "OK"}}]})
        return httpx.Response(404)

    report = await check_connection(_server(httpx.MockTransport(handler)), URL, "qwen3:8b", remote_hint=True)
    assert report.ok
    assert [step.id for step in report.steps] == ["connect", "server", "model", "reply"]
    assert sorted(report.models) == ["gemma4:e4b", "qwen3:8b"]
    assert report.steps[-1].detail == "«OK»"


async def test_the_server_error_is_shown_as_is() -> None:
    # NVIDIA PAIR contesta así cuando el equipo con los modelos no está conectado.
    transport = httpx.MockTransport(
        lambda r: httpx.Response(503, json={"error": "no active node selected or available"})
    )
    report = await check_connection(_server(transport), URL, "qwen3:8b", remote_hint=True)
    assert not report.ok
    connect, server = report.steps
    assert connect.ok
    assert not server.ok
    assert "no active node selected or available" in server.detail
    assert "otro equipo" in server.fix


async def test_an_unreachable_computer_explains_the_network() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    report = await check_connection(_server(httpx.MockTransport(refuse)), URL, "qwen3:8b", remote_hint=True)
    assert [step.id for step in report.steps] == ["connect"]
    assert "OLLAMA_HOST" in report.steps[0].fix


async def test_a_missing_model_stops_before_asking() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return _models("gemma4:e4b")

    report = await check_connection(_server(httpx.MockTransport(handler)), URL, "qwen3:8b", remote_hint=False)
    assert not report.ok
    assert report.steps[-1].id == "model"
    assert not any(path.endswith("/chat/completions") for path in asked)


async def test_a_key_is_needed() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "invalid api key"}}))
    report = await check_connection(_server(transport), URL, "qwen3:8b", remote_hint=True)
    assert "clave" in report.steps[-1].fix
