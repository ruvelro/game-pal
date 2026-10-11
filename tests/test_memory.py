import asyncio
import json
from pathlib import Path

import httpx
import pytest

from gamepal.bus import EventBus
from gamepal.config import AppConfig
from gamepal.events import AnswerUpdate, QuestionAsked
from gamepal.memory import MemoryService, MemoryStore, lesson_from
from gamepal.providers import LocalOpenAIProvider


@pytest.mark.parametrize(
    ("text", "lesson"),
    [
        ("Recuerda que Hécate es débil al fuego", "Hécate es débil al fuego"),
        ("Remember that I prefer the axe.", "I prefer the axe"),
        ("Acuérdate de que no me gustan los martillos", "no me gustan los martillos"),
        ("¿Qué recuerdas de ayer?", None),
        ("recuerda", None),
    ],
)
def test_lessons_are_detected(text: str, lesson: str | None) -> None:
    assert lesson_from(text) == lesson


def test_store_keeps_only_sessions_with_conversation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.sqlite")
    empty = store.start("hades-2")
    store.end(empty, None)
    used = store.start("hades-2")
    store.add_turn(used, "question", "¿Qué cojo?", "El rayo.")
    store.end(used, "Eligió rayo.")
    assert [s["id"] for s in store.sessions("hades-2")] == [used]
    assert store.summaries("hades-2", 3) == ["Eligió rayo."]
    exported = store.export("hades-2")
    assert exported["sessions"][0]["turns"][0]["answer"] == "El rayo."
    store.wipe("hades-2")
    assert store.sessions("hades-2") == []


def _provider(seen: list[dict]) -> LocalOpenAIProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Probó el hacha contra Hécate."}}]})

    return LocalOpenAIProvider("http://127.0.0.1:1/v1", "m", transport=httpx.MockTransport(handler))


async def test_service_records_summarizes_and_feeds_context(tmp_path: Path) -> None:
    bus, seen = EventBus(), []
    config = AppConfig.model_validate({"memory": {"enabled": True}})
    service = MemoryService(bus, tmp_path / "m.sqlite", config=lambda: config, provider=lambda: _provider(seen))
    await service.open("hades-2")
    await asyncio.sleep(0)
    bus.publish(QuestionAsked(id="q1", text="Recuerda que prefiero el hacha", source="voice"))
    bus.publish(QuestionAsked(id="q2", text="¿Cómo venzo a Hécate?", source="voice"))
    bus.publish(AnswerUpdate(id="q2", kind="question", title="¿Cómo venzo a Hécate?", text="Esquiva el anillo.",
                             done=True))  # fmt: skip
    await asyncio.sleep(0.05)
    assert service.session is not None
    turns = service.use(lambda s: s.turns(service.session))
    assert [t["question"] for t in turns] == ["¿Cómo venzo a Hécate?"]
    context = service.context()
    assert "prefiero el hacha" in context
    await service.close()
    assert "Esquiva el anillo" in seen[-1]["messages"][-1]["content"]

    # Siguiente sesión: el resumen anterior forma parte del contexto.
    await service.open("hades-2")
    assert "Probó el hacha contra Hécate." in service.context()
    await service.close()


async def test_disabled_memory_records_nothing(tmp_path: Path) -> None:
    bus = EventBus()
    service = MemoryService(bus, tmp_path / "m.sqlite", config=AppConfig, provider=lambda: None)
    await service.open("hades-2")
    assert service.session is None
    assert service.context() == ""
    await service.close()
    assert not (tmp_path / "m.sqlite").exists()
