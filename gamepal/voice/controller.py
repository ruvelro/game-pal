"""Controlador de voz: micrófono → detector de voz → frase clave o push-to-talk → transcripción.

Estados:
- idle: escucha por si se dice la frase clave.
- armed: tras la frase clave, espera la pregunta unos segundos.
- recording: push-to-talk pulsado, graba todo.
- transcribing: convierte la voz en texto.
Mientras el asistente habla, una frase de parada o la frase clave lo interrumpen.

La frase clave se busca al vuelo: en la primera pausa breve (o a los 2 s de hablar) se transcribe lo dicho
hasta ese momento, sin esperar a que termine la frase. Así el aviso de «te escucho» llega enseguida y, si
la pregunta va seguida («oye compañero, ¿qué planto?»), se graba entera en la misma frase.
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
# Para la frase clave basta el principio de lo dicho: transcribir frases largas enteras solo para buscarla
# gastaba CPU y retrasaba todo lo demás.
WAKE_HEAD_SECONDS = 2.5
# Una frase de parada es corta («para», «gracias»); lo largo que se oye mientras habla suele ser su propia voz.
STOP_MAX_SECONDS = 2.5
# Comprobación al vuelo: tras un poco de voz, en la primera pausa breve o al llevar un rato hablando.
EARLY_MIN_SPEECH = 0.6
EARLY_PAUSE = 0.25
EARLY_AFTER = 2.0
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
        self._work: asyncio.Queue[tuple[Utterance, str, int]] = asyncio.Queue(maxsize=4)
        # Frase en curso ya comprobada al vuelo, y la última en la que se oyó la frase clave así.
        self._early_checked = 0
        self._early_hit = 0
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
            self._enqueue(utterance, "push_to_talk", self._segmenter.segment)

    def ptt_toggle(self) -> None:
        if self._ptt:
            self.ptt_release()
        else:
            self.ptt_press()

    # Bucle ---------------------------------------------------------------------------------

    def _enqueue(self, utterance: Utterance, kind: str, segment: int) -> None:
        if self._work.full():
            log.warning("Se descarta una frase: la transcripción va con retraso")
            return
        self._work.put_nowait((utterance, kind, segment))

    def _check_early(self) -> None:
        """Busca la frase clave en lo que se lleva dicho, sin esperar al silencio final."""
        seg = self._segmenter
        if (
            not self._wake_enabled
            or not seg.active
            or seg.forced
            or self.state != "idle"
            or self._is_speaking()
            or self._early_checked == seg.segment
            or seg.speech_seconds < EARLY_MIN_SPEECH
            or (seg.silence_seconds < EARLY_PAUSE and seg.speech_seconds < EARLY_AFTER)
        ):
            return
        self._early_checked = seg.segment
        self._enqueue(Utterance(seg.snapshot(WAKE_HEAD_SECONDS)), "early", seg.segment)

    async def run(self) -> None:
        worker = asyncio.create_task(self._transcriber())
        self._set("idle")
        try:
            while True:
                frame = await self._frames.get()
                probability = self._vad.probability(frame)
                segment = self._segmenter.segment
                utterance = self._segmenter.feed(frame, probability)
                if utterance is None:
                    self._check_early()
                if self.state == "armed" and time.monotonic() > self._armed_until and not self._segmenter.active:
                    self._cue(False)
                    self._set("idle")
                if utterance is None or utterance.forced:
                    continue
                if self._early_hit == segment:
                    # La frase clave ya se oyó al vuelo en esta misma frase: puede llevar la pregunta detrás.
                    self._enqueue(utterance, "wake_full", segment)
                elif self.state == "armed":
                    self._set("transcribing")
                    self._enqueue(utterance, "question", segment)
                elif self._is_speaking() and utterance.seconds <= WAKE_MAX_SECONDS:
                    self._enqueue(utterance, "interrupt", segment)
                elif self._wake_enabled and utterance.seconds <= WAKE_MAX_SECONDS + 6:
                    self._enqueue(utterance, "wake", segment)
        finally:
            worker.cancel()

    async def _transcriber(self) -> None:
        while True:
            utterance, kind, segment = await self._work.get()
            try:
                await self._handle(utterance, kind, segment)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Error de transcripción")
                self._set("error", str(exc))
                self._set("idle")

    async def _transcribe(self, audio: Audio, hint: str, wake: bool = False) -> str:
        stt = self._wake_stt if wake else self._stt
        started = time.perf_counter()
        text = await asyncio.to_thread(stt.transcribe, audio, self._language, hint)
        log.debug("Transcripción de %.1f s en %.2f s: %r", len(audio) / 16000, time.perf_counter() - started, text)
        return text

    def _arm(self, cue: bool = True) -> None:
        self._armed_until = time.monotonic() + self._listen_seconds
        if self.state != "armed":
            if cue:
                self._cue(True)
            self._set("armed")

    async def _handle(self, utterance: Utterance, kind: str, segment: int) -> None:
        if kind in {"question", "push_to_talk"}:
            self._set("transcribing")
            started = time.perf_counter()
            text = await self._transcribe(utterance.audio, self._hotwords())
            self._set("idle")
            log.info("Pregunta transcrita en %.1f s: %r", time.perf_counter() - started, text)
            self._ask(text, "push_to_talk" if kind == "push_to_talk" else "voice")
            return
        if kind == "wake_full":
            # El aviso ya sonó al detectar la frase clave al vuelo.
            await self._wake_and_question(utterance, cued=True)
            return

        # Las frases clave como pista ayudan a que Whisper las escriba bien.
        hint = ", ".join(self._wake.phrases + self._stop.phrases)
        head = utterance.audio[: int(WAKE_HEAD_SECONDS * 16000)]
        text = await self._transcribe(head, hint, wake=True)
        if not text:
            return
        hit = self._wake.match_start(text)
        if kind == "early":
            if hit is not None and self._segmenter.segment == segment and self.state == "idle":
                # Aún se está hablando: se avisa ya y el resto de la frase será la pregunta.
                log.info("Frase clave detectada al vuelo: %r", text)
                self._early_hit = segment
                self._arm()
            return
        stop = self._stop.whole(text) if utterance.seconds <= STOP_MAX_SECONDS else None
        if kind == "interrupt" and (hit is not None or stop):
            self._bus.publish(SpeechStopped(reason="voice"))
            log.info("Respuesta interrumpida por voz: %r", text)
        if hit is None:
            return
        log.info("Frase clave detectada: %r", text)
        if len(hit.rest.split()) >= 2 or utterance.seconds > WAKE_HEAD_SECONDS:
            # Frase clave y pregunta seguidas: se transcribe todo con el modelo bueno.
            await self._wake_and_question(utterance)
            return
        self._arm()

    async def _wake_and_question(self, utterance: Utterance, cued: bool = False) -> None:
        """Una frase que empieza por la frase clave: lo que va detrás es la pregunta, si lo hay."""
        self._set("transcribing")
        started = time.perf_counter()
        full = await self._transcribe(utterance.audio, self._hotwords())
        hit = self._wake.match_start(full)
        # Si el modelo de las preguntas no escribe bien la frase clave, se quitan tantas palabras como tenga.
        words = len(self._wake.phrases[0].split()) if self._wake.phrases else 0
        rest = hit.rest if hit is not None else " ".join(full.split()[words:])
        if len(rest.split()) >= 2:
            self._set("idle")
            log.info("Pregunta transcrita en %.1f s: %r", time.perf_counter() - started, rest)
            self._ask(rest, "voice")
            return
        # Solo la frase clave: se espera la pregunta en la frase siguiente.
        self._set("idle")
        self._arm(cue=not cued)

    def _ask(self, text: str, source: Literal["voice", "push_to_talk"]) -> None:
        text = text.strip()
        if len(text) < 2 or not any(ch.isalnum() for ch in text):
            self._cue(False)
            return
        self._bus.publish(QuestionAsked(id=uuid.uuid4().hex[:12], text=text, source=source))
