"""Entrada de voz completa con modelos reales y voz sintética. Descarga modelos: se ejecuta con -m voice."""

import asyncio
from pathlib import Path

import pytest

from gamepal.bus import EventBus
from gamepal.events import QuestionAsked
from gamepal.perception.text import normalize
from gamepal.voice.controller import VoiceController
from gamepal.voice.models import SILERO_VAD, ModelStore, piper_artifacts
from gamepal.voice.stt import WhisperStt
from gamepal.voice.tts import PiperTts
from gamepal.voice.vad import SileroVad
from tests.fixtures.speech import frames, resample

pytestmark = pytest.mark.voice

CACHE = Path.home() / ".cache" / "game-pal-tests"


@pytest.fixture(scope="module")
def store() -> ModelStore:
    return ModelStore(CACHE / "models")


@pytest.fixture(scope="module")
def piper(store: ModelStore) -> PiperTts:
    model, meta = piper_artifacts("es_ES-davefx-medium")
    store.ensure(meta)
    return PiperTts(store.ensure(model))


def _say(piper: PiperTts, text: str) -> list:
    result = piper.synthesize(text)
    assert result is not None
    audio, rate = result
    return frames(resample(audio, rate))


async def _run(controller: VoiceController, queue: asyncio.Queue, bus: EventBus, chunks: list) -> QuestionAsked:
    async with bus.subscribe({"assistant.question"}) as sub:
        task = asyncio.create_task(controller.run())
        for chunk in chunks:
            await queue.put(chunk)
            await asyncio.sleep(0)
        event = await asyncio.wait_for(sub.get(), 60)
        task.cancel()
    assert isinstance(event, QuestionAsked)
    return event


async def test_wake_phrase_and_question_in_one_breath(store: ModelStore, piper: PiperTts) -> None:
    bus = EventBus()
    queue: asyncio.Queue = asyncio.Queue()
    controller = VoiceController(
        bus,
        queue,
        SileroVad(store.ensure(SILERO_VAD)),
        WhisperStt("base", download_root=str(CACHE / "whisper")),
        WhisperStt("tiny", download_root=str(CACHE / "whisper")),
        language="es",
        wake_phrases=["oye compañero"],
        stop_phrases=["gracias"],
        wake_enabled=True,
    )
    chunks = _say(piper, "Oye compañero, ¿qué bendición me conviene coger ahora?")
    event = await _run(controller, queue, bus, chunks)
    assert event.source == "voice"
    # Whisper base no siempre escribe igual la misma voz sintética («bendición», «medición», «bendiceme»);
    # lo que importa aquí es que llega la pregunta, sin la frase clave.
    assert "coger ahora" in normalize(event.text)
    assert "companero" not in normalize(event.text)


async def test_push_to_talk_records_without_wake_phrase(store: ModelStore, piper: PiperTts) -> None:
    bus = EventBus()
    queue: asyncio.Queue = asyncio.Queue()
    controller = VoiceController(
        bus,
        queue,
        SileroVad(store.ensure(SILERO_VAD)),
        WhisperStt("base", download_root=str(CACHE / "whisper")),
        None,
        language="es",
        wake_phrases=[],
        stop_phrases=[],
        wake_enabled=False,
    )
    chunks = _say(piper, "¿Cómo derroto a Cronos?")
    async with bus.subscribe({"assistant.question"}) as sub:
        task = asyncio.create_task(controller.run())
        controller.ptt_press()
        for chunk in chunks:
            await queue.put(chunk)
            await asyncio.sleep(0)
        while not queue.empty():  # noqa: ASYNC110 - espera a que el controlador consuma el audio
            await asyncio.sleep(0.01)
        controller.ptt_release()
        event = await asyncio.wait_for(sub.get(), 60)
        task.cancel()
    assert isinstance(event, QuestionAsked)
    assert event.source == "push_to_talk"
    assert "cronos" in normalize(event.text)  # sin tildes: a veces sale «acrónos»
