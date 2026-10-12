"""El controlador de voz con un detector y un Whisper falsos: el orden de las cosas, sin modelos."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import numpy as np

from gamepal.bus import EventBus, Subscription
from gamepal.events import Event, QuestionAsked, SpeechStopped
from gamepal.perception.vocabulary import Vocabulary
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


EXPECT_S = 5.0


async def expect(sub: Subscription, count: int = 1) -> list[Event]:
    """Los `count` eventos que se esperan, con margen: en una CI lenta la transcripción (en otro hilo) tarda."""
    return [await asyncio.wait_for(sub.get(), EXPECT_S) for _ in range(count)]


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
        await feed(controller, queue, frames((VOICE, 0.8), (QUIET, 0.35), (VOICE, 1.2), (QUIET, 1.4)))
        [question] = await expect(sub)
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
        await feed(controller, queue, frames((VOICE, 0.9), (QUIET, 1.4), (VOICE, 1.6), (QUIET, 1.4)))
        [question] = await expect(sub)
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
        [stopped] = await expect(sub)
    assert isinstance(stopped, SpeechStopped)


async def test_its_order_at_the_end_of_its_own_voice_stops_it() -> None:
    bus = EventBus()
    # Mientras habla, su voz y la del jugador llegan en una sola frase; la orden va al final.
    controller, _, queue = make(
        bus, ScriptedStt(lambda s: "Úsala para invierno. Parar."), ScriptedStt(lambda s: ""), is_speaking=lambda: True
    )
    async with bus.subscribe({"voice.stopped"}) as sub:
        await feed(controller, queue, frames((VOICE, 4.0), (QUIET, 1.0)))
        [stopped] = await expect(sub)
    assert isinstance(stopped, SpeechStopped)


async def test_a_single_word_is_not_a_question_and_a_cut_question_waits() -> None:
    bus = EventBus()
    answers = iter(["Oye compañero, nada.", "Oye compañero, ¿podemos con…", "la azada?"])
    controller, _, queue = make(bus, ScriptedStt(lambda s: "oye compañero"), ScriptedStt(lambda s: next(answers)))
    async with bus.subscribe({"assistant.question"}) as sub:
        await feed(controller, queue, frames((VOICE, 1.2), (QUIET, 1.4)))
        assert await received(sub) == []
        await feed(controller, queue, frames((VOICE, 1.8), (QUIET, 1.4), (VOICE, 0.8), (QUIET, 1.4)))
        [question] = await expect(sub)
    assert isinstance(question, QuestionAsked)
    assert question.text == "podemos con la azada?"


async def test_question_heard_on_the_fly_is_not_lost_when_little_follows() -> None:
    bus = EventBus()
    # Sin pausa: la comprobación al vuelo llega a los 2 s y ya oye el principio de la pregunta; la frase
    # termina poco después. Antes se daba por «solo la frase clave» y la pregunta se perdía.
    wake = ScriptedStt(lambda s: "oye compañero, qué bendición me")
    main = ScriptedStt(lambda s: "Oye compañero, ¿qué bendición me conviene?")
    controller, _, queue = make(bus, wake, main)
    async with bus.subscribe({"assistant.question"}) as sub:
        task = asyncio.create_task(controller.run())
        # Todo el audio llega antes de que termine la comprobación al vuelo, como en una prueba rápida.
        for chunk in frames((VOICE, 2.3), (QUIET, 1.5)):
            queue.put_nowait(chunk)
        [question] = await expect(sub)
        task.cancel()
    assert isinstance(question, QuestionAsked)
    assert question.text == "qué bendición me conviene?"


async def test_after_an_answer_it_listens_without_the_wake_phrase() -> None:
    bus = EventBus()
    controller, cues, queue = make(bus, ScriptedStt(lambda s: "nada"), ScriptedStt(lambda s: "Sí."))
    async with bus.subscribe({"assistant.question"}) as sub:
        task = asyncio.create_task(controller.run())
        await asyncio.sleep(0)
        controller.follow_up(5)
        assert controller.state == "armed"
        for chunk in frames((VOICE, 0.6), (QUIET, 1.4)):
            await queue.put(chunk)
            await asyncio.sleep(0.001)
        [answer] = await expect(sub)
        task.cancel()
    assert isinstance(answer, QuestionAsked)
    # Una palabra vale cuando se contesta a game-pal, y la pregunta va marcada para llevar la conversación.
    assert answer.text == "Sí."
    assert answer.follow_up
    assert cues == []  # sin pitidos: se sigue la conversación


async def test_follow_up_ends_quietly_when_nothing_is_said() -> None:
    bus = EventBus()
    controller, cues, queue = make(bus, ScriptedStt(lambda s: ""), ScriptedStt(lambda s: ""))
    task = asyncio.create_task(controller.run())
    await asyncio.sleep(0)
    controller.follow_up(0.05)
    await asyncio.sleep(0.1)
    for chunk in frames((QUIET, 0.3)):
        await queue.put(chunk)
    await asyncio.sleep(0.05)
    task.cancel()
    assert controller.state == "idle"
    assert cues == []  # no se pidió nada: no hay pitido de «cancelado»


async def test_voice_service_listens_once_the_answer_has_been_spoken(tmp_path: Path) -> None:
    from gamepal.events import AnswerUpdate, SpeakingChanged
    from gamepal.voice.service import VoiceService

    bus = EventBus()
    service = VoiceService(bus, tmp_path, vocabulary=Vocabulary, toggle_proactive=lambda: False)
    calls: list[float] = []

    class Controller:
        def follow_up(self, seconds: float) -> None:
            calls.append(seconds)

    class Speech:
        speaking = True

    service.controller = Controller()  # type: ignore[assignment]
    service.speech = Speech()  # type: ignore[assignment]
    task = asyncio.create_task(service._follow_ups(6))
    await asyncio.sleep(0.01)
    bus.publish(QuestionAsked(id="q", text="¿Qué planto?", source="voice"))
    bus.publish(AnswerUpdate(id="q", kind="question", text="Chirivías. ¿Te digo dónde?", done=True,
                             outputs=["voice"]))  # fmt: skip
    await asyncio.sleep(0.01)
    assert calls == []  # aún habla: si escuchara ya, se oiría a sí mismo
    bus.publish(SpeakingChanged(speaking=False))
    await asyncio.sleep(0.01)
    assert calls == [9.0]  # acaba en pregunta: unos segundos más
    task.cancel()
