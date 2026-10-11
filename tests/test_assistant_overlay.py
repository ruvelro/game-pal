import asyncio
import json
from pathlib import Path

import httpx
import pytest

from gamepal.assistant import Assistant
from gamepal.bus import EventBus
from gamepal.config import AppConfig, ProviderConfig
from gamepal.events import AnswerUpdate, QuestionAsked, ScreenDetected
from gamepal.overlay.model import OverlayModel
from gamepal.profiles import LoadedProfile, load_profile
from gamepal.providers import LocalOpenAIProvider


def _provider(answer: str, seen: list[dict]) -> LocalOpenAIProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body.get("response_format"):
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"route": "quick"}'}}]})
        words = answer.split(" ")
        chunks = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': w + ' '}}]})}\n\n" for w in words)
        return httpx.Response(200, text=chunks + "data: [DONE]\n\n")

    return LocalOpenAIProvider("http://127.0.0.1:1/v1", "main", transport=httpx.MockTransport(handler))


@pytest.fixture
def hades(repo_profiles: Path) -> LoadedProfile:
    return load_profile(repo_profiles / "hades-2", bundled=True)


def _assistant(bus: EventBus, provider: LocalOpenAIProvider, config: AppConfig, profile: LoadedProfile,
               screen: ScreenDetected | None = None) -> Assistant:  # fmt: skip
    return Assistant(
        bus,
        config=lambda: config,
        provider=lambda: provider,
        profile=lambda: profile,
        screen=lambda: screen,
        read=lambda: None,
    )


async def _collect(bus: EventBus, trigger, until_done: int = 1) -> list[AnswerUpdate]:  # type: ignore[no-untyped-def]
    events: list[AnswerUpdate] = []
    async with bus.subscribe({"assistant.answer"}) as sub:
        trigger()
        while sum(e.done for e in events) < until_done:
            event = await asyncio.wait_for(sub.get(), 5)
            assert isinstance(event, AnswerUpdate)
            events.append(event)
    return events


async def test_question_streams_answer_with_screen_context(hades: LoadedProfile) -> None:
    bus, seen = EventBus(), []
    screen = ScreenDetected(screen_id="boon_choice", name="Elección", data={"options": [{"name": "Rayo"}]})
    config = AppConfig.model_validate({"output": {"answers": ["overlay", "text", "voice"]}})
    assistant = _assistant(bus, _provider("Coge el Rayo.", seen), config, hades, screen)
    runner = asyncio.create_task(assistant.run())
    await asyncio.sleep(0)
    events = await _collect(bus, lambda: bus.publish(QuestionAsked(id="q1", text="¿Cuál cojo?", source="voice")))
    runner.cancel()
    assert events[-1].text.strip() == "Coge el Rayo."
    assert events[-1].outputs == ["overlay", "text", "voice"]
    prompt = seen[-1]["messages"]
    assert "Hades II" in prompt[0]["content"]
    assert "español" in prompt[0]["content"]
    assert '<pantalla id="boon_choice"' in prompt[-1]["content"]


async def test_text_questions_are_not_spoken(hades: LoadedProfile) -> None:
    bus, seen = EventBus(), []
    config = AppConfig.model_validate({"output": {"answers": ["overlay", "text", "voice"]}})
    assistant = _assistant(bus, _provider("Hola.", seen), config, hades)
    runner = asyncio.create_task(assistant.run())
    await asyncio.sleep(0)
    events = await _collect(bus, lambda: assistant.ask("hola"))
    runner.cancel()
    assert "voice" not in events[-1].outputs


async def test_fast_model_routes_quick_answers(hades: LoadedProfile) -> None:
    bus, seen = EventBus(), []
    config = AppConfig(provider=ProviderConfig(model="main", fast_model="tiny"))
    assistant = _assistant(bus, _provider("De nada.", seen), config, hades)
    runner = asyncio.create_task(assistant.run())
    await asyncio.sleep(0)
    events = await _collect(bus, lambda: bus.publish(QuestionAsked(id="q", text="gracias", source="voice")))
    runner.cancel()
    assert events[-1].route == "quick"
    assert seen[-1]["model"] == "tiny"


async def test_proactive_fires_once_per_entry(hades: LoadedProfile) -> None:
    bus, seen = EventBus(), []
    assistant = _assistant(bus, _provider("Coge el Rayo.", seen), AppConfig(), hades)
    runner = asyncio.create_task(assistant.run())
    await asyncio.sleep(0)
    enter = ScreenDetected(screen_id="boon_choice", name="Elección", change="enter", frame_id=1)
    events = await _collect(bus, lambda: bus.publish(enter))
    assert events[-1].kind == "proactive"
    assert events[-1].outputs == ["overlay"]  # la voz no está entre las salidas elegidas
    # Una actualización de la misma pantalla no repite el aviso.
    bus.publish(ScreenDetected(screen_id="boon_choice", change="update", frame_id=2))
    await asyncio.sleep(0.05)
    assert len([s for s in seen if not s.get("response_format")]) == 1
    runner.cancel()


async def test_proactive_can_be_muted(hades: LoadedProfile) -> None:
    bus, seen = EventBus(), []
    assistant = _assistant(bus, _provider("x", seen), AppConfig(), hades)
    assistant.proactive_muted = True
    runner = asyncio.create_task(assistant.run())
    await asyncio.sleep(0)
    bus.publish(ScreenDetected(screen_id="boss_intro", change="enter", frame_id=1))
    await asyncio.sleep(0.05)
    runner.cancel()
    assert seen == []


def test_overlay_places_card_in_game_corner() -> None:
    model = OverlayModel(corner="top-right", width=400, linger=5)
    model.handle({"topic": "capture.window", "data": {"left": 100, "top": 50, "width": 1600, "height": 900}})
    base = {"id": "a", "title": "Tu pregunta", "outputs": ["overlay"]}
    model.handle({"topic": "assistant.answer", "data": {**base, "text": ""}}, now=0)
    assert model.view(now=0).status == "Pensando…"  # type: ignore[union-attr]
    model.handle({"topic": "assistant.answer", "data": {**base, "text": "Coge el Rayo.", "done": True}}, now=1)
    view = model.view(now=2)
    assert view is not None
    assert (view.x, view.y, view.body) == (100 + 1600 - 400 - 18, 50 + 18, "Coge el Rayo.")
    assert model.view(now=7) is None  # desaparece tras `linger`


def test_overlay_ignores_answers_not_meant_for_it_and_shows_listening() -> None:
    model = OverlayModel()
    model.handle({"topic": "assistant.answer", "data": {"id": "a", "text": "hola", "outputs": ["text"]}})
    assert model.view() is None
    model.handle({"topic": "voice.state", "data": {"state": "armed"}})
    view = model.view()
    assert view is not None
    assert view.status == "Te escucho…"
