"""Núcleo de game-pal: crea los componentes según la configuración y los mantiene vivos."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from gamepal.activity import ActivityStore
from gamepal.assistant import Assistant
from gamepal.bus import EventBus
from gamepal.capture import CaptureBackend, CaptureUnavailableError, create_backend
from gamepal.config import AppConfig, load_config, save_config
from gamepal.events import ScreenDetected, ScreenRead, StatusChanged
from gamepal.evidence.service import EvidenceService
from gamepal.hardware import HardwareInfo, detect
from gamepal.keystore import SecretStore
from gamepal.knowledge import KnowledgeService
from gamepal.memory import MemoryService
from gamepal.overlay.process import OverlayProcess
from gamepal.paths import AppPaths, bundled_profiles_dir
from gamepal.perception.ocr import OcrEngine, create_engine
from gamepal.perception.screens import ScreenInterpreter
from gamepal.perception.text import normalize
from gamepal.perception.vision import VisionReader
from gamepal.perception.vocabulary import Entry, Vocabulary, VocabularySource, build_vocabulary, find_install_dir
from gamepal.perception.watcher import ScreenWatcher, Target
from gamepal.profiles import LoadedProfile, ProfileError, ProfileStore
from gamepal.providers import Provider, create_provider
from gamepal.providers.hub import ProviderHub, model_for
from gamepal.runtime.catalog import bundled_catalog
from gamepal.runtime.service import EngineService
from gamepal.voice.service import VoiceService

log = logging.getLogger(__name__)


@dataclass
class Components:
    backend: CaptureBackend | None = None
    ocr: OcrEngine | None = None
    watcher: ScreenWatcher | None = None
    watcher_task: asyncio.Task[None] | None = None
    provider: Provider | None = None
    profile: LoadedProfile | None = None
    vocabulary: Vocabulary = field(default_factory=Vocabulary)
    install_dir: Path | None = None
    interpreter: ScreenInterpreter | None = None
    interpreter_task: asyncio.Task[None] | None = None
    errors: dict[str, str] = field(default_factory=dict)


class Daemon:
    def __init__(
        self,
        paths: AppPaths,
        bus: EventBus | None = None,
        *,
        backend_factory: Callable[[], CaptureBackend] = create_backend,
        bundled_dir: Path | None = None,
        secrets: SecretStore | None = None,
        hardware: Callable[[], HardwareInfo] = detect,
    ) -> None:
        self.paths = paths
        self._backend_factory = backend_factory
        self.bus = bus or EventBus()
        self.config: AppConfig = load_config(paths.config_file)
        self.secrets = secrets or SecretStore()
        self.activity = ActivityStore(paths.data_dir / "activity.sqlite")
        self.engine = EngineService(
            self.bus,
            paths.data_dir / "engine",
            paths.log_dir,
            bundled_catalog(),
            config=lambda: self.config,
            on_change=self._create_provider,
            hardware=hardware,
        )
        self.hub = ProviderHub(
            self.bus,
            config=lambda: self.config,
            secrets=self.secrets,
            activity=self.activity,
            local_endpoint=lambda: self.engine.endpoint,
        )
        self.profiles = ProfileStore(bundled_dir or bundled_profiles_dir(), paths.user_profiles_dir)
        self.c = Components()
        self.last_read: ScreenRead | None = None
        self.current_screen: ScreenDetected | None = None
        self._lock = asyncio.Lock()
        self._listener: asyncio.Task[None] | None = None
        self._gui: tuple[int, str] | None = None
        self.knowledge = KnowledgeService(
            self.bus,
            paths.data_dir,
            paths.cache_dir,
            config=lambda: self.config,
            provider=self._local_provider,
        )
        self.evidence = EvidenceService(self.bus, config=lambda: self.config, vocabulary=lambda: self.c.vocabulary)
        self.memory = MemoryService(
            self.bus, paths.data_dir / "memory.sqlite", config=lambda: self.config, provider=lambda: self.c.provider
        )
        self.assistant = Assistant(
            self.bus,
            config=lambda: self.config,
            provider=lambda: self.c.provider,
            profile=lambda: self.c.profile,
            screen=lambda: self.current_screen,
            read=lambda: self.last_read,
            retrieve=self.knowledge.search,
            has_knowledge=lambda: self.knowledge.store is not None and self.knowledge.store.stats()["chunks"] > 0,
            state=self._state_context,
            fast=lambda: self.hub.route("fast"),
            fast_model=lambda: None,
            fast_enabled=self._fast_enabled,
        )
        self._assistant_task: asyncio.Task[None] | None = None
        self.voice = VoiceService(
            self.bus,
            paths.cache_dir,
            vocabulary=lambda: self.c.vocabulary,
            toggle_proactive=self._toggle_proactive,
        )
        self.overlay: OverlayProcess | None = None
        self._voice_started = False

    def _local_provider(self) -> Provider | None:
        """Los embeddings de conocimiento siempre se calculan en local."""
        try:
            return self.hub.client("local")
        except Exception:
            return None

    def _fast_enabled(self) -> bool:
        """El enrutado rápido solo compensa si la tarea rápida usa otro modelo distinto del principal."""
        routing = self.config.routing
        fast = (routing.fast.provider, model_for(self.config, routing.fast.provider, "fast"))
        main = (routing.reasoning.provider, model_for(self.config, routing.reasoning.provider, "reasoning"))
        return bool(fast[1]) and fast != main

    def vision_enabled(self) -> bool:
        route = self.config.routing.vision
        if not model_for(self.config, route.provider, "vision"):
            return False
        # Una captura de pantalla solo sale del equipo si el jugador lo ha permitido.
        return route.provider == "local" or self.config.cloud.allow_images

    def _state_context(self) -> str:
        return "\n\n".join(part for part in (self.evidence.ledger.prompt(), self.memory.context()) if part)

    def attach_gui(self, port: int, token: str) -> None:
        """El overlay se conecta a la misma API que la GUI."""
        self._gui = (port, token)

    def _toggle_proactive(self) -> bool:
        self.assistant.proactive_muted = not self.assistant.proactive_muted
        return self.assistant.proactive_muted

    # Ciclo de vida -------------------------------------------------------------------------

    async def start(self) -> None:
        self.bus.publish(StatusChanged(component="daemon", state="starting"))
        self._listener = asyncio.create_task(self._remember_reads())
        self._assistant_task = asyncio.create_task(self.assistant.run(), name="assistant")
        await self.apply(self.config, persist=False)
        self.bus.publish(StatusChanged(component="daemon", state="running"))

    async def stop(self) -> None:
        if self.overlay is not None:
            await self.overlay.stop()
        await self.voice.stop()
        await self.knowledge.close()
        await self.evidence.close()
        await self.memory.close()
        await self.assistant.aclose()
        if self._assistant_task is not None:
            self._assistant_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._assistant_task
        await self._stop_interpreter()
        await self._stop_watcher()
        await self.hub.aclose()
        await self.engine.stop()
        self.activity.close()
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener

    async def _remember_reads(self) -> None:
        async with self.bus.subscribe({"perception"}, maxsize=8) as sub:
            async for event in sub:
                if isinstance(event, ScreenRead):
                    self.last_read = event
                elif isinstance(event, ScreenDetected):
                    self.current_screen = event if event.screen_id else None

    # Configuración --------------------------------------------------------------------------

    async def apply(self, config: AppConfig, persist: bool = True) -> None:
        """Aplica una configuración nueva recreando solo lo necesario."""
        async with self._lock:
            previous = self.config
            self.config = config
            if persist:
                save_config(config, self.paths.config_file)
            first = self.c.backend is None and "capture" not in self.c.errors

            profile_changed = first or config.profile != previous.profile or self.c.profile is None
            if profile_changed:
                self._load_profile()
                await self.knowledge.open(self.c.profile)
            if (
                profile_changed
                or config.game_dirs.get(config.profile) != previous.game_dirs.get(config.profile)
                or config.game_language != previous.game_language
            ):
                await self._load_vocabulary()
            if (
                profile_changed
                or config.save_dirs != previous.save_dirs
                or config.trusted_plugins != previous.trusted_plugins
            ):
                await self.evidence.open(self.c.profile)
            if profile_changed or config.memory.enabled != previous.memory.enabled:
                await self.memory.open(self.c.profile.profile.id if self.c.profile else None)
            if first or config.ocr != previous.ocr or config.game_language != previous.game_language:
                await self._create_ocr()
            if first or config.provider != previous.provider or config.runtime != previous.runtime:
                await self.engine.sync()
            if (
                first
                or config.provider != previous.provider
                or config.cloud != previous.cloud
                or config.routing != previous.routing
            ):
                await self._create_provider()
            if first:
                self._create_backend()
            await self._restart_watcher()
            await self._restart_interpreter()
            voice_changed = (
                config.voice != previous.voice
                or config.output.answers != previous.output.answers
                or config.output.proactive != previous.output.proactive
                or config.output_language != previous.output_language
            )
            if not self._voice_started or voice_changed:
                await self.voice.stop()
                await self.voice.start(config)
                self._voice_started = True
            await self._apply_overlay(previous, first)

    def _load_profile(self) -> None:
        try:
            self.c.profile = self.profiles.get(self.config.profile)
            self.c.errors.pop("profile", None)
            self.bus.publish(StatusChanged(component="profile", state="ready", detail=self.c.profile.profile.name))
        except ProfileError as exc:
            self.c.profile = None
            self.c.errors["profile"] = str(exc)
            self.bus.publish(StatusChanged(component="profile", state="error", detail=str(exc)))

    async def _apply_overlay(self, previous: AppConfig, first: bool) -> None:
        if self._gui is None:
            return
        wanted = self.config.output.overlay.enabled
        changed = (
            self.config.output.overlay != previous.output.overlay
            or self.config.output_language != previous.output_language
        )
        if self.overlay is not None and (not wanted or changed):
            await self.overlay.stop()
            self.overlay = None
        if wanted and self.overlay is None:
            self.overlay = OverlayProcess(self.bus, *self._gui)
            self.overlay.start()

    async def _load_vocabulary(self) -> None:
        self.c.vocabulary, self.c.install_dir = Vocabulary(), None
        loaded = self.c.profile
        if loaded is None:
            return
        profile = loaded.profile
        install = find_install_dir(profile, self.config.game_dirs.get(profile.id))
        languages = list(dict.fromkeys([self.config.game_language, "en", *profile.game.languages_supported]))
        try:
            self.c.vocabulary = await asyncio.to_thread(build_vocabulary, profile, loaded.root, install, languages)
            self.c.install_dir = install
            self.c.errors.pop("vocabulary", None)
        except (OSError, ValueError) as exc:
            log.warning("No se pudo cargar el vocabulario: %s", exc)
            self.c.errors["vocabulary"] = str(exc)
        self._add_knowledge_titles()
        sets = self.c.vocabulary.sets()
        if sets:
            summary = ", ".join(f"{name} ({count})" for name, count in sets.items())
            log.info("Vocabulario de %s: %s", profile.name, summary)

    def _add_knowledge_titles(self) -> None:
        """Los títulos de las páginas de las wikis son nombres del juego: ayudan a corregir el OCR."""
        store = self.knowledge.store
        if store is None:
            return
        titles = store.titles()
        for title in titles:
            key = normalize(title).replace(" ", "_")
            if key and len(title) <= 60:
                self.c.vocabulary.add(Entry(key, "knowledge", {"*": title}))
        if titles:
            self.c.vocabulary.sources.append(VocabularySource("knowledge", "fuentes de conocimiento", len(titles)))

    async def _create_ocr(self) -> None:
        self.bus.publish(StatusChanged(component="ocr", state="starting", detail="Cargando motor OCR…"))
        try:
            self.c.ocr = await asyncio.to_thread(create_engine, self.config.ocr, [self.config.game_language])
            self.c.errors.pop("ocr", None)
            self.bus.publish(StatusChanged(component="ocr", state="ready", detail=self.c.ocr.name))
        except Exception as exc:
            log.exception("No se pudo crear el motor OCR")
            self.c.ocr = None
            self.c.errors["ocr"] = str(exc)
            self.bus.publish(StatusChanged(component="ocr", state="error", detail=str(exc)))

    async def _create_provider(self) -> None:
        await self.hub.reset()
        try:
            create_provider(self.config.provider)  # valida la dirección del servidor local
            self.c.provider = self.hub.route("reasoning")
            self.c.errors.pop("provider", None)
            self.bus.publish(StatusChanged(component="provider", state="idle", detail=self.routing_summary()))
        except Exception as exc:
            self.c.provider = None
            self.c.errors["provider"] = str(exc)
            self.bus.publish(StatusChanged(component="provider", state="error", detail=str(exc)))

    def routing_summary(self) -> str:
        parts = []
        for role, label in (("reasoning", "razonar"), ("fast", "rápido"), ("vision", "visión")):
            route = getattr(self.config.routing, role)
            model = model_for(self.config, route.provider, role) or "sin modelo"  # type: ignore[arg-type]
            if role == "fast" and not self._fast_enabled():
                continue
            if role == "vision" and not self.vision_enabled():
                continue
            backup = f" → {route.fallback}" if route.fallback != "none" else ""
            parts.append(f"{label}: {route.provider} ({model}){backup}")
        return " · ".join(parts)

    async def refresh_providers(self) -> None:
        """Tras guardar o borrar una clave."""
        await self._create_provider()

    def _create_backend(self) -> None:
        try:
            self.c.backend = self._backend_factory()
            self.c.errors.pop("capture", None)
        except CaptureUnavailableError as exc:
            self.c.backend = None
            self.c.errors["capture"] = str(exc)
            self.bus.publish(StatusChanged(component="capture", state="error", detail=str(exc)))

    def target(self) -> Target:
        if self.config.window_title:
            return Target((), self.config.window_title)
        if self.c.profile is None:
            return Target((), None)
        game = self.c.profile.profile.game
        return Target(tuple(game.process), game.window_title)

    async def _stop_watcher(self) -> None:
        task = self.c.watcher_task
        self.c.watcher_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _stop_interpreter(self) -> None:
        task = self.c.interpreter_task
        self.c.interpreter_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _restart_interpreter(self) -> None:
        await self._stop_interpreter()
        watcher, loaded = self.c.watcher, self.c.profile
        if watcher is None or loaded is None or not loaded.profile.screens:
            self.c.interpreter = None
            return
        vision = None
        if self.vision_enabled():
            vision = VisionReader(self.hub.route("vision"), "", loaded.profile, self.c.vocabulary)
        self.c.interpreter = ScreenInterpreter(
            self.bus,
            loaded.profile,
            self.c.vocabulary,
            self.config.output_language,
            watcher.frame_for_read,
            watcher.request_read,
            vision,
        )
        self.c.interpreter_task = asyncio.create_task(self.c.interpreter.run(), name="interpreter")

    async def _restart_watcher(self) -> None:
        was_paused = self.c.watcher.paused if self.c.watcher else False
        await self._stop_watcher()
        if self.c.backend is None or self.c.ocr is None:
            self.c.watcher = None
            return
        self.c.watcher = ScreenWatcher(
            self.bus, self.c.backend, self.c.ocr, self.config.capture, self.target(), paused=was_paused
        )
        self.c.watcher_task = asyncio.create_task(self.c.watcher.run(), name="watcher")

    # Acciones ------------------------------------------------------------------------------

    def pause(self) -> None:
        if self.c.watcher is not None:
            self.c.watcher.pause()

    def resume(self) -> None:
        if self.c.watcher is not None:
            self.c.watcher.resume()

    def config_path(self) -> Path:
        return self.paths.config_file
