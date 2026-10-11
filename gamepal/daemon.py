"""Núcleo de game-pal: crea los componentes según la configuración y los mantiene vivos."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from gamepal.bus import EventBus
from gamepal.capture import CaptureBackend, CaptureUnavailableError, create_backend
from gamepal.config import AppConfig, load_config, save_config
from gamepal.events import ScreenDetected, ScreenRead, StatusChanged
from gamepal.paths import AppPaths, bundled_profiles_dir
from gamepal.perception.ocr import OcrEngine, create_engine
from gamepal.perception.screens import ScreenInterpreter
from gamepal.perception.vision import VisionReader
from gamepal.perception.vocabulary import Vocabulary, build_vocabulary, find_install_dir
from gamepal.perception.watcher import ScreenWatcher, Target
from gamepal.profiles import LoadedProfile, ProfileError, ProfileStore
from gamepal.providers import Provider, create_provider

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
    ) -> None:
        self.paths = paths
        self._backend_factory = backend_factory
        self.bus = bus or EventBus()
        self.config: AppConfig = load_config(paths.config_file)
        self.profiles = ProfileStore(bundled_dir or bundled_profiles_dir(), paths.user_profiles_dir)
        self.c = Components()
        self.last_read: ScreenRead | None = None
        self.current_screen: ScreenDetected | None = None
        self._lock = asyncio.Lock()
        self._listener: asyncio.Task[None] | None = None

    # Ciclo de vida -------------------------------------------------------------------------

    async def start(self) -> None:
        self.bus.publish(StatusChanged(component="daemon", state="starting"))
        self._listener = asyncio.create_task(self._remember_reads())
        await self.apply(self.config, persist=False)
        self.bus.publish(StatusChanged(component="daemon", state="running"))

    async def stop(self) -> None:
        await self._stop_interpreter()
        await self._stop_watcher()
        if self.c.provider is not None:
            await self.c.provider.aclose()
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
            if (
                profile_changed
                or config.game_dirs.get(config.profile) != previous.game_dirs.get(config.profile)
                or config.game_language != previous.game_language
            ):
                await self._load_vocabulary()
            if first or config.ocr != previous.ocr or config.game_language != previous.game_language:
                await self._create_ocr()
            if first or config.provider != previous.provider:
                await self._create_provider()
            if first:
                self._create_backend()
            await self._restart_watcher()
            await self._restart_interpreter()

    def _load_profile(self) -> None:
        try:
            self.c.profile = self.profiles.get(self.config.profile)
            self.c.errors.pop("profile", None)
            self.bus.publish(StatusChanged(component="profile", state="ready", detail=self.c.profile.profile.name))
        except ProfileError as exc:
            self.c.profile = None
            self.c.errors["profile"] = str(exc)
            self.bus.publish(StatusChanged(component="profile", state="error", detail=str(exc)))

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
        sets = self.c.vocabulary.sets()
        if sets:
            summary = ", ".join(f"{name} ({count})" for name, count in sets.items())
            log.info("Vocabulario de %s: %s", profile.name, summary)

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
        if self.c.provider is not None:
            await self.c.provider.aclose()
            self.c.provider = None
        try:
            self.c.provider = create_provider(self.config.provider)
            self.c.errors.pop("provider", None)
            detail = self.config.provider.model or "sin modelo seleccionado"
            self.bus.publish(StatusChanged(component="provider", state="idle", detail=detail))
        except Exception as exc:
            self.c.errors["provider"] = str(exc)
            self.bus.publish(StatusChanged(component="provider", state="error", detail=str(exc)))

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
        if self.c.provider is not None and self.config.provider.vision_model:
            vision = VisionReader(self.c.provider, self.config.provider.vision_model, loaded.profile, self.c.vocabulary)
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
