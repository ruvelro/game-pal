"""Modelos que razonan: margen para pensar, apagarlo bajo demanda y no quedarse nunca en blanco."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from gamepal.assistant import Assistant
from gamepal.bus import EventBus
from gamepal.config import AppConfig
from gamepal.events import AnswerUpdate, QuestionAsked
from gamepal.explain import OUT_OF_ROOM, explain_error
from gamepal.overlay.model import OverlayModel, Rect
from gamepal.profiles import LoadedProfile, load_profile
from gamepal.providers import LocalOpenAIProvider, Message, ProviderError
from gamepal.providers.openai_compat import FORCED_THINK_TOKENS, strip_think

URL = "http://127.0.0.1:11434/v1"
Reply = Callable[[dict[str, Any]], httpx.Response]


def sse(*deltas: dict[str, Any], finish: str = "stop") -> httpx.Response:
    chunks = [{"choices": [{"delta": d, "finish_reason": None}]} for d in deltas]
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body)


def server(reply: Reply, seen: list[dict[str, Any]], show: dict[str, Any] | None = None) -> httpx.MockTransport:
    """Un servidor de modelos. Con `show` hace de Ollama; sin él, de uno que no dice si el modelo razona."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json=show) if show is not None else httpx.Response(404)
        body = json.loads(request.content)
        seen.append(body)
        return reply(body)

    return httpx.MockTransport(handler)


THINKS = {"capabilities": ["completion", "thinking"], "details": {"family": "qwen35moe"}}
PLAIN = {"capabilities": ["completion"], "details": {"family": "gemma3"}}
GPT_OSS = {"capabilities": ["completion", "thinking"], "details": {"family": "gptoss"}}


async def _text(provider: LocalOpenAIProvider, **kwargs: Any) -> str:
    return "".join([d async for d in provider.chat_stream([Message("user", "¿Qué planto?")], **kwargs)])


async def test_ollama_reasoning_model_gets_room_to_think_and_none_for_internal_tasks() -> None:
    seen: list[dict[str, Any]] = []
    transport = server(lambda b: sse({"content": "Chirivías."}), seen, THINKS)
    provider = LocalOpenAIProvider(URL, "qwen3.6", transport=transport)
    assert await _text(provider, max_tokens=500, think=2048) == "Chirivías."
    assert seen[-1]["max_tokens"] == 500 + 2048
    assert "reasoning_effort" not in seen[-1]
    await _text(provider, max_tokens=80)  # una tarea interna: sin pensar
    assert seen[-1]["max_tokens"] == 80
    assert seen[-1]["reasoning_effort"] == "none"
    assert seen[-1]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_models_that_do_not_reason_keep_the_answer_limit() -> None:
    seen: list[dict[str, Any]] = []
    provider = LocalOpenAIProvider(URL, "gemma3", transport=server(lambda b: sse({"content": "Hola."}), seen, PLAIN))
    await _text(provider, max_tokens=500, think=2048)
    await _text(provider, max_tokens=80)
    assert [b["max_tokens"] for b in seen] == [500, 80]
    assert all("reasoning_effort" not in b for b in seen)


async def test_models_that_always_think_get_room_even_for_internal_tasks() -> None:
    seen: list[dict[str, Any]] = []
    provider = LocalOpenAIProvider(URL, "gpt-oss", transport=server(lambda b: sse({"content": "Sí."}), seen, GPT_OSS))
    await _text(provider, max_tokens=80)
    assert seen[-1]["max_tokens"] == 80 + FORCED_THINK_TOKENS
    assert "reasoning_effort" not in seen[-1]


async def test_an_answer_that_runs_out_thinking_is_asked_again_without_thinking() -> None:
    def reply(body: dict[str, Any]) -> httpx.Response:
        if body.get("reasoning_effort") == "none":
            return sse({"content": "Cuatro días."})
        return sse({"reasoning": "Veamos, la chirivía…"}, finish="length")

    seen: list[dict[str, Any]] = []
    provider = LocalOpenAIProvider(URL, "razonador", transport=server(reply, seen))
    assert await _text(provider, max_tokens=500, think=2048) == "Cuatro días."
    assert [b.get("reasoning_effort") for b in seen] == [None, "none"]
    assert (await provider.traits()).reasons is True
    await _text(provider, max_tokens=500, think=2048)  # ya se sabe que razona: lleva el margen desde el principio
    assert seen[2]["max_tokens"] == 500 + 2048


async def test_servers_that_reject_the_switch_stop_receiving_it() -> None:
    def reply(body: dict[str, Any]) -> httpx.Response:
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "unknown field reasoning_effort"}})
        if body["stream"]:
            return sse({"content": "Vale."})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Vale."}, "finish_reason": "stop"}]})

    seen: list[dict[str, Any]] = []
    provider = LocalOpenAIProvider(URL, "estricto", transport=server(reply, seen))
    result = await provider.chat([Message("user", "hola")], max_tokens=80)
    assert result.text == "Vale."
    assert (await provider.traits()).can_disable is False
    await _text(provider, max_tokens=80)
    assert "reasoning_effort" not in seen[-1]


async def test_gives_up_with_a_clear_error_instead_of_a_blank_answer() -> None:
    seen: list[dict[str, Any]] = []
    always = server(lambda b: sse({"reasoning": "…"}, finish="length"), seen, GPT_OSS)
    provider = LocalOpenAIProvider(URL, "gpt-oss", transport=always)
    with pytest.raises(ProviderError, match=OUT_OF_ROOM):
        await _text(provider, max_tokens=500, think=1024)
    assert len(seen) == 3
    assert seen[1]["max_tokens"] > seen[0]["max_tokens"]  # cada intento con más margen
    assert "Dale más margen" in explain_error(f"local: {OUT_OF_ROOM}", AppConfig())


@pytest.mark.parametrize(
    "pieces",
    [["<think>pienso", " mucho</think>\n\nHola."], ["<thi", "nk>pienso</th", "ink>Hola."], ["Hola."], ["Hol", "a."]],
)
async def test_think_tags_never_reach_the_player(pieces: list[str]) -> None:
    deltas = [{"content": p} for p in pieces]
    provider = LocalOpenAIProvider(URL, "etiquetas", transport=server(lambda b: sse(*deltas), []))
    assert await _text(provider, max_tokens=500) == "Hola."


async def test_short_answers_that_look_like_a_tag_are_kept() -> None:
    provider = LocalOpenAIProvider(URL, "corto", transport=server(lambda b: sse({"content": "<"}), []))
    assert await _text(provider, max_tokens=20) == "<"


def test_strip_think_in_whole_answers() -> None:
    assert strip_think("<think>a\nb</think>\nRespuesta") == ("Respuesta", True)
    assert strip_think("<think>sin cerrar") == ("", True)
    assert strip_think("<think>\n\n</think>\n\nHola") == ("Hola", False)  # Qwen 3 sin pensar
    assert strip_think("Sin etiquetas") == ("Sin etiquetas", False)


async def test_an_empty_think_block_does_not_count_as_thinking() -> None:
    seen: list[dict[str, Any]] = []
    reply = server(lambda b: sse({"content": "<think>\n\n</think>"}, {"content": "\n\nHola."}), seen)
    provider = LocalOpenAIProvider(URL, "qwen3", transport=reply)
    assert await _text(provider, max_tokens=80) == "Hola."
    traits = await provider.traits()
    assert traits.can_disable is not False  # se le pidió no pensar y no pensó: se puede seguir apagando
    await _text(provider, max_tokens=80)
    assert seen[-1]["reasoning_effort"] == "none"


# Asistente --------------------------------------------------------------------------------------


@pytest.fixture
def stardew(repo_profiles: Path) -> LoadedProfile:
    return load_profile(repo_profiles / "stardew-valley", bundled=True)


async def _ask(config: AppConfig, provider: LocalOpenAIProvider, profile: LoadedProfile) -> AnswerUpdate:
    bus = EventBus()
    assistant = Assistant(
        bus, config=lambda: config, provider=lambda: provider, profile=lambda: profile,
        screen=lambda: None, read=lambda: None,
    )  # fmt: skip
    runner = asyncio.create_task(assistant.run())
    await asyncio.sleep(0)
    async with bus.subscribe({"assistant.answer"}) as sub:
        bus.publish(QuestionAsked(id="q", text="¿Qué planto hoy?", source="text"))
        while True:
            event = await asyncio.wait_for(sub.get(), 5)
            assert isinstance(event, AnswerUpdate)
            if event.done:
                runner.cancel()
                return event


async def test_answers_follow_the_length_and_thinking_settings(stardew: LoadedProfile) -> None:
    seen: list[dict[str, Any]] = []
    transport = server(lambda b: sse({"content": "Fresas."}), seen, THINKS)
    provider = LocalOpenAIProvider(URL, "qwen3.6", transport=transport)
    config = AppConfig.model_validate(
        {"generation": {"answer_tokens": 1000, "thinking": True, "thinking_tokens": 4096}}
    )
    assert (await _ask(config, provider, stardew)).text == "Fresas."
    assert seen[-1]["max_tokens"] == 1000 + 4096
    config.generation.thinking = False
    await _ask(config, provider, stardew)
    assert seen[-1]["max_tokens"] == 1000
    assert seen[-1]["reasoning_effort"] == "none"


async def test_a_blank_answer_is_reported(stardew: LoadedProfile) -> None:
    provider = LocalOpenAIProvider(URL, "mudo", transport=server(lambda b: sse({"content": "  "}), [], PLAIN))
    event = await _ask(AppConfig(), provider, stardew)
    assert "no ha devuelto ninguna respuesta" in event.error


# Overlay -----------------------------------------------------------------------------------------


def test_overlay_stays_inside_the_screen() -> None:
    model = OverlayModel(corner="top-right", width=420, screen=Rect(0, 34, 1710, 991))
    model.handle({"topic": "assistant.answer", "data": {"id": "a", "outputs": ["overlay"], "text": "Hola"}}, now=0)
    view = model.view(now=0)
    assert view is not None
    assert (view.x, view.y) == (1710 - 420 - 18, 34 + 18)
    # Una ventana de juego que asoma por la derecha no arrastra la tarjeta fuera de la pantalla.
    model.handle({"topic": "capture.window", "data": {"left": 900, "top": 0, "width": 1600, "height": 900}})
    view = model.view(now=0)
    assert view is not None
    assert view.x + view.width <= 1710 - 18
