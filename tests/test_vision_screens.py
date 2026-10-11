import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from gamepal.bus import EventBus
from gamepal.capture.base import Frame
from gamepal.events import ScreenDetected, ScreenRead
from gamepal.perception.screens import ScreenInterpreter
from gamepal.perception.vision import VisionReader, build_schema
from gamepal.perception.vocabulary import build_vocabulary
from gamepal.profiles import LoadedProfile, load_profile
from gamepal.providers import LocalOpenAIProvider
from tests.conftest import GAME, line, pattern
from tests.fixtures.synthetic import write_game_text

OCR = [line("Boons of Zeus", 0.36, 0.06), line("Storm Lash", 0.28, 0.25), line("Ataque +20%", 0.28, 0.31)]


@pytest.fixture
def hades(repo_profiles: Path) -> LoadedProfile:
    return load_profile(repo_profiles / "hades-2", bundled=True)


def _reader(hades: LoadedProfile, tmp_path: Path, answer: dict[str, Any]) -> VisionReader:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404)  # como un servidor que no es Ollama: no dice si el modelo razona
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    provider = LocalOpenAIProvider("http://127.0.0.1:1/v1", "vl", transport=httpx.MockTransport(handler))
    vocab = build_vocabulary(hades.profile, hades.root, write_game_text(tmp_path), ["en", "es"])
    return VisionReader(provider, "vl", hades.profile, vocab)


def test_schema_limits_screens(hades: LoadedProfile) -> None:
    screens = [s for s in hades.profile.screens if s.id in {"boon_choice", "pom_choice"}]
    schema = build_schema(screens)
    assert schema["properties"]["screen"]["enum"] == ["boon_choice", "pom_choice", "other"]
    assert set(schema["properties"]["items"]["items"]["required"]) == {"rarity", "name", "effect"}


async def test_vision_drops_invented_content(hades: LoadedProfile, tmp_path: Path) -> None:
    reader = _reader(
        hades,
        tmp_path,
        {
            "screen": "boon_choice",
            "confidence": 2.0,
            "items": [
                {"name": "Storm Lash", "rarity": "Rare", "effect": "Ataque +20%"},
                {"name": "Espada Inventada", "rarity": "Mítico", "effect": "Daño infinito"},
            ],
        },
    )
    screen = next(s for s in hades.profile.screens if s.id == "boon_choice")
    result = await reader.read(pattern(1), OCR, [screen], "es")
    assert result is not None
    assert result.confidence == 0.95
    assert result.data["options"] == [
        {
            "name": {"id": "ZeusWeaponBoon", "name": "Latigazo de Tormenta", "seen": "Storm Lash"},
            "rarity": {"id": "rare", "name": "Raro", "seen": "Rare"},
            "effect": "Ataque +20%",
        }
    ]


async def test_vision_rejects_screens_outside_candidates(hades: LoadedProfile, tmp_path: Path) -> None:
    reader = _reader(hades, tmp_path, {"screen": "victory", "confidence": 0.9, "items": []})
    screen = next(s for s in hades.profile.screens if s.id == "boon_choice")
    assert await reader.read(pattern(1), OCR, [screen], "es") is None


async def test_interpreter_publishes_and_requests_rereads(hades: LoadedProfile) -> None:
    bus = EventBus()
    vocab = build_vocabulary(hades.profile, hades.root, None, ["en"])
    rereads: list[int] = []
    frames = {1: Frame(1, pattern(1), GAME)}
    interpreter = ScreenInterpreter(bus, hades.profile, vocab, "es", frames.get, lambda: rereads.append(1), vision=None)
    boss = [line("Chronos", 0.4, 0.5), line("Titan of Time", 0.38, 0.6)]
    async with bus.subscribe({"perception.screen"}) as sub:
        await interpreter.handle(ScreenRead(frame_id=1, backend="t", elapsed_ms=1, lines=boss))
        event = await sub.get()
        assert isinstance(event, ScreenDetected)
        assert event.screen_id == "boss_intro"
        assert event.data["boss"]["id"] == "chronos"
        await interpreter.handle(ScreenRead(frame_id=2, backend="t", elapsed_ms=1, lines=[]))
        assert rereads == [1]
        await interpreter.handle(ScreenRead(frame_id=3, backend="t", elapsed_ms=1, lines=[]))
        left = await sub.get()
        assert isinstance(left, ScreenDetected)
        assert left.screen_id is None
