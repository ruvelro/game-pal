"""Monta la voz según la configuración: salida hablada, micrófono, atajos y mando."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path

from gamepal.bus import EventBus
from gamepal.config import AppConfig
from gamepal.downloads import MB, FolderProgress, describe
from gamepal.events import ChatToggled, StatusChanged
from gamepal.perception.vocabulary import Vocabulary
from gamepal.voice.audio import Microphone, Speaker, tone
from gamepal.voice.controller import VoiceController
from gamepal.voice.inputs import Gamepad, HotkeyError, Hotkeys, bind_loop
from gamepal.voice.models import PIPER_DEFAULT_VOICES, SILERO_VAD, ModelStore, piper_artifacts
from gamepal.voice.speech import SpeechOutput
from gamepal.voice.stt import LocalServerStt, Stt, WhisperStt
from gamepal.voice.tts import PiperTts, SystemTts, Tts, espeak_data_dir, piper_works
from gamepal.voice.vad import EnergyVad, SileroVad, Vad

log = logging.getLogger(__name__)

MAX_HOTWORDS = 60
# Tamaño aproximado de cada modelo de Whisper (CTranslate2), para mostrar el progreso de la descarga.
WHISPER_SIZES_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1530, "large-v3-turbo": 1620}


class VoiceService:
    def __init__(
        self,
        bus: EventBus,
        cache_dir: Path,
        *,
        vocabulary: Callable[[], Vocabulary],
        toggle_proactive: Callable[[], bool],
    ) -> None:
        self._bus = bus
        self._cache_dir = cache_dir
        self._store = ModelStore(cache_dir / "models")
        self._whisper_dir = cache_dir / "whisper"
        self._vocabulary = vocabulary
        self._toggle_proactive = toggle_proactive
        self._tasks: list[asyncio.Task[None]] = []
        self._mic: Microphone | None = None
        self._hotkeys: Hotkeys | None = None
        self.gamepad: Gamepad | None = None
        self.speaker: Speaker | None = None
        self.speech: SpeechOutput | None = None
        self.controller: VoiceController | None = None
        self.tts: Tts | None = None

    def _status(self, state: str, detail: str = "", progress: float | None = None) -> None:
        event = StatusChanged(component="voice", state=state, detail=detail, progress=progress)  # type: ignore[arg-type]
        self._bus.publish(event)

    def _progress(self, name: str, received: int, total: int | None) -> None:
        if total and received % (4 << 20) < 65536:
            self._status("starting", describe(name, received, total / MB), received / total)

    # Construcción --------------------------------------------------------------------------

    def _build_tts(self, config: AppConfig) -> Tts | None:
        tts = config.voice.tts
        language = config.output_language
        if tts.backend == "none":
            return None
        data_dir = espeak_data_dir() if tts.backend == "piper" else None
        if tts.backend == "piper" and not piper_works(self._cache_dir, data_dir):
            # Mejor la voz del sistema que una aplicación que se cierra al hablar.
            self._status("starting", "La voz de Piper no funciona en este equipo: se usa la del sistema")
            return SystemTts(language, tts.rate)
        if tts.backend == "piper":
            voice = tts.voice or PIPER_DEFAULT_VOICES.get(language) or PIPER_DEFAULT_VOICES["en"]
            model, meta = piper_artifacts(voice)
            self._status("starting", f"Preparando la voz {voice}…")
            self._store.ensure(meta, self._progress)
            path = self._store.ensure(model, self._progress)
            return PiperTts(path, tts.rate, data_dir)
        return SystemTts(language, tts.rate)

    def _build_vad(self) -> Vad:
        try:
            return SileroVad(self._store.ensure(SILERO_VAD, self._progress))
        except Exception as exc:
            log.warning("Silero VAD no disponible (%s); se usa el detector por energía", exc)
            return EnergyVad()

    def _build_stt(self, config: AppConfig, model: str) -> Stt:
        stt = config.voice.stt
        if stt.backend == "local-openai":
            return LocalServerStt(stt.base_url, model)
        self._status("starting", f"Cargando Whisper {model}…")
        name = f"Whisper {model}"

        def report(received: int, fraction: float | None) -> None:
            self._status("starting", describe(name, received, WHISPER_SIZES_MB.get(model)), fraction)

        # La primera vez, faster-whisper descarga el modelo sin informar del progreso.
        with FolderProgress(self._whisper_dir, report, WHISPER_SIZES_MB.get(model)):
            return WhisperStt(model, download_root=str(self._whisper_dir))

    def _hotwords(self) -> str:
        names: list[str] = []
        vocabulary = self._vocabulary()
        for set_name in vocabulary.sets():
            for entry in vocabulary.entries(set_name)[:MAX_HOTWORDS]:
                names.extend(entry.names.values())
                if len(names) >= MAX_HOTWORDS:
                    return ", ".join(dict.fromkeys(names))
        return ", ".join(dict.fromkeys(names))

    # Ciclo de vida -------------------------------------------------------------------------

    async def start(self, config: AppConfig) -> None:
        loop = asyncio.get_running_loop()
        wants_speech = "voice" in config.output.answers or config.voice.enabled
        try:
            if wants_speech:
                self.tts = await asyncio.to_thread(self._build_tts, config)
                if self.tts is not None:
                    self.speaker = Speaker(volume=config.voice.tts.volume)
                    self.speech = SpeechOutput(self._bus, self.tts, self.speaker)
                    self._tasks.append(asyncio.create_task(self.speech.run(), name="speech"))
            if config.voice.enabled:
                await self._start_input(config, loop)
            self._start_hotkeys(config, loop)
            self._status("ready", self._summary(config))
        except Exception as exc:
            log.exception("No se pudo iniciar la voz")
            self._status("error", str(exc))

    async def _start_input(self, config: AppConfig, loop: asyncio.AbstractEventLoop) -> None:
        voice = config.voice
        language = voice.language or config.output_language
        vad = await asyncio.to_thread(self._build_vad)
        stt = await asyncio.to_thread(self._build_stt, config, voice.stt.model)
        wake_stt = None
        if voice.wake.enabled and voice.stt.wake_model and voice.stt.wake_model != voice.stt.model:
            wake_stt = await asyncio.to_thread(self._build_stt, config, voice.stt.wake_model)
        self._mic = Microphone(voice.input_device)
        self._mic.start()
        speech = self.speech
        speaker = self.speaker

        def cue(rising: bool) -> None:
            if speaker is not None:
                speaker.play(*tone(rising))

        self.controller = VoiceController(
            self._bus,
            self._mic.queue,
            vad,
            stt,
            wake_stt,
            language=language,
            wake_phrases=voice.wake.phrases,
            stop_phrases=voice.stop_phrases,
            wake_enabled=voice.wake.enabled,
            listen_seconds=voice.wake.listen_seconds,
            vad_threshold=voice.vad_threshold,
            hotwords=self._hotwords,
            is_speaking=lambda: speech is not None and speech.speaking,
            cue=cue,
        )
        self._tasks.append(asyncio.create_task(self.controller.run(), name="voice"))
        ptt = voice.push_to_talk
        if ptt.enabled and ptt.gamepad_button:
            controller = self.controller
            press = controller.ptt_toggle if ptt.mode == "toggle" else controller.ptt_press
            release = (lambda: None) if ptt.mode == "toggle" else controller.ptt_release
            self.gamepad = Gamepad(ptt.gamepad_button, bind_loop(loop, press), bind_loop(loop, release))
            self.gamepad.start()

    def _start_hotkeys(self, config: AppConfig, loop: asyncio.AbstractEventLoop) -> None:
        hotkeys = Hotkeys()
        bound = False
        try:
            ptt = config.voice.push_to_talk
            if self.controller is not None and ptt.enabled and ptt.key:
                c = self.controller
                if ptt.mode == "toggle":
                    hotkeys.bind(ptt.key, bind_loop(loop, c.ptt_toggle))
                else:
                    hotkeys.bind(ptt.key, bind_loop(loop, c.ptt_press), bind_loop(loop, c.ptt_release))
                bound = True
            proactive = config.output.proactive
            if proactive.enabled and proactive.mute_key:

                def toggle() -> None:
                    muted = self._toggle_proactive()
                    self._bus.publish(
                        StatusChanged(
                            component="hotkeys", state="ready",
                            detail="Avisos silenciados" if muted else "Avisos activos",
                        )
                    )  # fmt: skip

                hotkeys.bind(proactive.mute_key, bind_loop(loop, toggle))
                bound = True
            overlay = config.output.overlay
            if overlay.enabled and overlay.chat_key:
                hotkeys.bind(overlay.chat_key, bind_loop(loop, lambda: self._bus.publish(ChatToggled())))
                bound = True
            if bound:
                hotkeys.start()
                self._hotkeys = hotkeys
                self._bus.publish(StatusChanged(component="hotkeys", state="ready", detail="Atajos activos"))
        except (HotkeyError, OSError, ImportError) as exc:
            log.warning("Atajos no disponibles: %s", exc)
            self._bus.publish(StatusChanged(component="hotkeys", state="error", detail=str(exc)))

    def _summary(self, config: AppConfig) -> str:
        parts = []
        if self.tts is not None:
            parts.append(f"voz {self.tts.name}")
        if self.controller is not None:
            modes = []
            if config.voice.wake.enabled:
                modes.append("frase clave")
            if config.voice.push_to_talk.enabled:
                modes.append("pulsar para hablar")
            parts.append("escucha: " + (", ".join(modes) or "solo GUI"))
        return " · ".join(parts) or "voz desactivada"

    async def stop(self) -> None:
        if self._hotkeys is not None:
            self._hotkeys.stop()
            self._hotkeys = None
        if self.gamepad is not None:
            self.gamepad.stop()
            self.gamepad = None
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        if self._mic is not None:
            self._mic.stop()
            self._mic = None
        if self.speech is not None:
            self.speech.stop()
        self.controller = None
        self.speech = None
        self.speaker = None
        self.tts = None
