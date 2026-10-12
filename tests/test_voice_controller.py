"""El controlador de voz con un detector y un Whisper falsos: el orden de las cosas, sin modelos."""

import asyncio
from collections.abc import Callable

import numpy as np

from gamepal.bus import EventBus, Subscription
from gamepal.events import Event, QuestionAsked, SpeechStopped
from gamepal.voice.controller import VoiceController
from gamepal.voice.vad import FRAME, FRAME_SECONDS, Audio

VOICE = np.full(FRAME, 0.1, dtype=np.float32)
QUIET = np.zeros(FRAME, dtype=np.float32)


class LoudVad:
    """Hay voz si el frame no está en silencio."""

    def probability(self, frame: Audio) -> float:
        return 0.9 if float(np.abs(frame).max()) > 0 else 0.0

    def reset(self) -> None:
        return None


class ScriptedStt:
    def __init__(self, reply: Callable[[float], str]) -> None:
        self.name = "falso"
        self.calls: list[float] = []
        self._reply = reply

    def transcribe(self, audio: Audio, language: str | None, hotwords: str | None = None) -> str:
        seconds = len(audio) / 16000
        self.calls.append(seconds)
        return self._reply(seconds)


async def received(sub: Subscription) -> list[Event]:
    """Lo que ha llegado a la suscripción hasta ahora."""
    events: list[Event] = []
    while True:
        try:
            events.append(await asyncio.wait_for(sub.get(), 0.05))
        except TimeoutError:
            return events


def frames(*parts: tuple[Audio, float]) -> list[Audio]:
    out: list[Audio] = []
    for frame, seconds in parts:
        out += [frame] * round(seconds / FRAME_SECONDS)
    return out


async def feed(controller: VoiceController, queue: asyncio.Queue[Audio], chunks: list[Audio]) -> None:
    task = asyncio.create_task(controller.run())
    for chunk in chunks:
        await queue.put(chunk)
        # Deja correr al transcriptor, como pasaría con audio en tiempo real.
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.05)
    task.cancel()


def make(
    bus: EventBus, wake: ScriptedStt, main: ScriptedStt, **kwargs: object
) -> tuple[VoiceController, list[bool], asyncio.Queue[Audio]]:
    queue: asyncio.Queue[Audio] = asyncio.Queue()
    cues: list[bool] = []
    controller = VoiceController(
        bus, queue, LoudVad(), main, wake, language="es", wake_phrases=["oye compañero"],
        stop_phrases=["gracias", "para"], wake_enabled=True, cue=cues.append, **kwargs,  # type: ignore[arg-type]
    )  # fmt: skip
    return controller, cues, queue


async def test_wake_phrase_is_heard_on_the_first_pause_and_the_question_follows() -> None:
    bus = EventBus()
    wake = ScriptedStt(lambda seconds: "oye compañero")
    main = ScriptedStt(lambda seconds: "Oye compañero, ¿qué planto en verano?")
    controller, cues, queue = make(bus, wake, main)
    async with bus.subscribe({"assistant.question"}) as sub:
        # «Oye compañero», pausa corta (menos que el silencio que cierra la frase) y la pregunta.
        await feed(controller, queue, frames((VOICE, 0.8), (QUIET, 0.35), (VOICE, 1.2), (QUIET, 1.0)))
        [question] = await received(sub)
    assert isinstance(question, QuestionAsked)
    assert question.text == "qué planto en verano?"
    assert cues == [True]  # un solo aviso, el de «te escucho»
    # Al vuelo solo se transcribe el principio; la frase entera, una vez, con el modelo de las preguntas.
    assert wake.calls
    assert max(wake.calls) < 1.5
    assert len(main.calls) == 1


async def test_only_the_wake_phrase_waits_for_the_question() -> None:
    bus = EventBus()
    controller, cues, queue = make(
        bus,
        ScriptedStt(lambda s: "oye compañero"),
        ScriptedStt(lambda s: "Oye compañero." if s < 1.5 else "¿Qué planto?"),
    )
    async with bus.subscribe({"assistant.question"}) as sub:
        await feed(controller, queue, frames((VOICE, 0.9), (QUIET, 1.0), (VOICE, 1.6), (QUIET, 1.0)))
        [question] = await received(sub)
    assert isinstance(question, QuestionAsked)
    assert question.text == "¿Qué planto?"
    assert cues == [True]


async def test_its_own_voice_does_not_stop_it_but_a_short_stop_phrase_does() -> None:
    bus = EventBus()
    replies = iter(["Úsala para invierno y luego vuelve a casa.", "para"])
    controller, _, queue = make(
        bus, ScriptedStt(lambda s: next(replies)), ScriptedStt(lambda s: ""), is_speaking=lambda: True
    )
    async with bus.subscribe({"voice.stopped"}) as sub:
        await feed(controller, queue, frames((VOICE, 2.0), (QUIET, 1.0)))
        assert await received(sub) == []
        await feed(controller, queue, frames((VOICE, 0.5), (QUIET, 1.0)))
        [stopped] = await received(sub)
    assert isinstance(stopped, SpeechStopped)
