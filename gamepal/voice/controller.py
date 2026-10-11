"""Controlador de voz: micrófono → detector de voz → frase clave o push-to-talk → transcripción.

Estados:
- idle: escucha por si se dice la frase clave.
- armed: tras la frase clave, espera la pregunta unos segundos.
- recording: push-to-talk pulsado, graba todo.
- transcribing: convierte la voz en texto.
Mientras el asistente habla, una frase de parada o la frase clave lo interrumpen.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Literal

from gamepal.bus import EventBus
from gamepal.events import QuestionAsked, SpeechStopped, VoiceState
from gamepal.voice.phrases import PhraseMatcher
from gamepal.voice.stt import Stt
from gamepal.voice.vad import Audio, Segmenter, Utterance, Vad

log = logging.getLogger(__name__)

WAKE_MAX_SECONDS = 6.0
StateName = Literal["off", "idle", "armed", "recording", "transcribing", "error"]


class VoiceController:
    def __init__(
        self,
        bus: EventBus,
        frames: asyncio.Queue[Audio],
        vad: Vad,
        stt: Stt,
        wake_stt: Stt | None,
        *,
        language: str,
        wake_phrases: list[str],
        stop_phrases: list[str],
        wake_enabled: bool,
        listen_seconds: float = 7.0,
        vad_threshold: float = 0.5,
        hotwords: Callable[[], str] | None = None,
        is_speaking: Callable[[], bool] = lambda: False,
        cue: Callable[[bool], None] = lambda rising: None,
    ) -> None:
        self._bus = bus
        self._frames = frames
        self._vad = vad
        self._stt = stt
        self._wake_stt = wake_stt or stt
        self._language = language
        self._wake = PhraseMatcher(wake_phrases)
        self._stop = PhraseMatcher(stop_phrases)
        self._wake_enabled = wake_enabled and bool(wake_phrases)
        self._listen_seconds = listen_seconds
        self._segmenter = Segmenter(threshold=vad_threshold)
        self._hotwords = hotwords or (lambda: "")
        self._is_speaking = is_speaking
        self._cue = cue
        self._armed_until = 0.0
        self._ptt = False
        self._work: asyncio.Queue[tuple[Utterance, str]] = asyncio.Queue(maxsize=4)
        self.state: StateName = "idle"

    def _set(self, state: StateName, detail: str = "") -> None:
        if state != self.state or detail:
            self.state = state
            self._bus.publish(VoiceState(state=state, detail=detail))

    # Push-to-talk --------------------------------------------------------------------------

    def ptt_press(self) -> None:
        if self._ptt:
            return
        self._ptt = True
        if self._is_speaking():
            self._bus.publish(SpeechStopped(reason="push_to_talk"))
        self._segmenter.force_start()
        self._cue(True)
        self._set("recording")

    def ptt_release(self) -> None:
        if not self._ptt:
            return
        self._ptt = False
        utterance = self._segmenter.force_end()
        self._set("idle")
        if utterance is not None and utterance.seconds >= 0.3:
            self._enqueue(utterance, "push_to_talk")

    def ptt_toggle(self) -> None:
        if self._ptt:
            self.ptt_release()
        else:
            self.ptt_press()

    # Bucle ---------------------------------------------------------------------------------

    def _enqueue(self, utterance: Utterance, kind: str) -> None:
        if self._work.full():
            log.warning("Se descarta una frase: la transcripción va con retraso")
            return
        self._work.put_nowait((utterance, kind))

    async def run(self) -> None:
        worker = asyncio.create_task(self._transcriber())
        self._set("idle")
        try:
            while True:
                frame = await self._frames.get()
                probability = self._vad.probability(frame)
                utterance = self._segmenter.feed(frame, probability)
                if self.state == "armed" and time.monotonic() > self._armed_until and not self._segmenter.active:
                    self._cue(False)
                    self._set("idle")
                if utterance is None or utterance.forced:
                    continue
                if self.state == "armed":
                    self._set("transcribing")
                    self._enqueue(utterance, "question")
                elif self._is_speaking() and utterance.seconds <= WAKE_MAX_SECONDS:
                    self._enqueue(utterance, "interrupt")
                elif self._wake_enabled and utterance.seconds <= WAKE_MAX_SECONDS + 6:
                    self._enqueue(utterance, "wake")
        finally:
            worker.cancel()

    async def _transcriber(self) -> None:
        while True:
            utterance, kind = await self._work.get()
            try:
                await self._handle(utterance, kind)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Error de transcripción")
                self._set("error", str(exc))
                self._set("idle")

    async def _handle(self, utterance: Utterance, kind: str) -> None:
        if kind in {"question", "push_to_talk"}:
            self._set("transcribing")
            text = await asyncio.to_thread(self._stt.transcribe, utterance.audio, self._language, self._hotwords())
            self._set("idle")
            self._ask(text, "push_to_talk" if kind == "push_to_talk" else "voice")
            return

        # Las frases clave como pista ayudan a que Whisper las escriba bien.
        hint = ", ".join(self._wake.phrases + self._stop.phrases)
        text = await asyncio.to_thread(self._wake_stt.transcribe, utterance.audio, self._language, hint)
        if not text:
            return
        hit = self._wake.match_start(text)
        if kind == "interrupt" and (hit is not None or self._stop.contains(text)):
            self._bus.publish(SpeechStopped(reason="voice"))
            log.info("Respuesta interrumpida por voz: %r", text)
        if hit is None:
            return
        log.info("Frase clave detectada: %r", text)
        if len(hit.rest.split()) >= 2:
            # Frase clave y pregunta seguidas: se transcribe todo con el modelo bueno.
            self._set("transcribing")
            full = await asyncio.to_thread(self._stt.transcribe, utterance.audio, self._language, self._hotwords())
            question = self._wake.match_start(full)
            self._set("idle")
            self._ask(question.rest if question else hit.rest, "voice")
            return
        self._armed_until = time.monotonic() + self._listen_seconds
        self._cue(True)
        self._set("armed")

    def _ask(self, text: str, source: Literal["voice", "push_to_talk"]) -> None:
        text = text.strip()
        if len(text) < 2 or not any(ch.isalnum() for ch in text):
            self._cue(False)
            return
        self._bus.publish(QuestionAsked(id=uuid.uuid4().hex[:12], text=text, source=source))
