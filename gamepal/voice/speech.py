"""Salida hablada: convierte las respuestas en streaming en voz, frase a frase."""

from __future__ import annotations

import asyncio
import logging

from gamepal.bus import EventBus
from gamepal.events import AnswerUpdate, SpeakingChanged, SpeechStopped
from gamepal.voice.audio import Speaker
from gamepal.voice.tts import SentenceBuffer, Tts

log = logging.getLogger(__name__)


class SpeechOutput:
    def __init__(self, bus: EventBus, tts: Tts, speaker: Speaker | None) -> None:
        self._bus = bus
        self._tts = tts
        self._speaker = speaker
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._current: str | None = None
        self._buffers: dict[str, SentenceBuffer] = {}
        self._speaking = False
        self._generation = 0

    @property
    def speaking(self) -> bool:
        return self._speaking or (self._speaker is not None and self._speaker.busy) or not self._queue.empty()

    def stop(self) -> None:
        self._generation += 1
        while not self._queue.empty():
            self._queue.get_nowait()
        self._buffers.clear()
        self._current = None
        if self._speaker is not None:
            self._speaker.stop()
        self._tts.stop()

    def say(self, text: str) -> None:
        """Habla un texto suelto (prueba de voz desde la GUI)."""
        self.stop()
        buffer = SentenceBuffer()
        for sentence in [*buffer.feed(text), *buffer.flush()]:
            self._queue.put_nowait(("say", sentence))

    async def run(self) -> None:
        player = asyncio.create_task(self._player())
        try:
            async with self._bus.subscribe({"assistant.answer", "voice.stopped"}) as sub:
                async for event in sub:
                    if isinstance(event, SpeechStopped):
                        self.stop()
                    elif isinstance(event, AnswerUpdate) and "voice" in event.outputs:
                        self._on_answer(event)
        finally:
            player.cancel()

    def _on_answer(self, event: AnswerUpdate) -> None:
        if event.id != self._current:
            # Una respuesta nueva corta a la anterior.
            self.stop()
            self._current = event.id
        buffer = self._buffers.setdefault(event.id, SentenceBuffer())
        sentences = buffer.feed(event.delta) if event.delta else []
        if event.done:
            sentences += buffer.flush()
            self._buffers.pop(event.id, None)
        for sentence in sentences:
            self._queue.put_nowait((event.id, sentence))

    async def _player(self) -> None:
        while True:
            _, sentence = await self._queue.get()
            generation = self._generation
            if not self._speaking:
                self._speaking = True
                self._bus.publish(SpeakingChanged(speaking=True))
            try:
                audio = await asyncio.to_thread(self._tts.synthesize, sentence)
                if generation != self._generation:
                    continue
                if audio is None:
                    await asyncio.to_thread(self._tts.speak_blocking, sentence)
                elif self._speaker is not None:
                    # Se encola y se sigue sintetizando la siguiente frase mientras esta suena.
                    self._speaker.play(*audio)
            except Exception:
                log.exception("No se pudo hablar")
            finally:
                if self._queue.empty():
                    if self._speaker is not None:
                        await asyncio.to_thread(self._speaker.wait_idle)
                    if self._queue.empty() and self._speaking:
                        self._speaking = False
                        self._bus.publish(SpeakingChanged(speaking=False))
