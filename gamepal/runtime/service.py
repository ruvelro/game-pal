"""Servicio del motor local: instala, descarga y mantiene el servidor acorde a la configuración."""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gamepal.bus import EventBus
from gamepal.config import AppConfig
from gamepal.events import DownloadProgress, StatusChanged
from gamepal.hardware import HardwareInfo, detect, propose_tier
from gamepal.runtime.catalog import Catalog, ModelEntry, RuntimeVariant
from gamepal.runtime.downloads import DownloadCancelled
from gamepal.runtime.llama import EngineError, LlamaRuntime

log = logging.getLogger(__name__)

PROGRESS_INTERVAL_S = 0.4


@dataclass(frozen=True, slots=True)
class _Plan:
    variant: str
    models: tuple[str, ...]
    context: int
    models_max: int
    preload: str | None


class EngineService:
    def __init__(
        self,
        bus: EventBus,
        root: Path,
        log_dir: Path,
        catalog: Catalog,
        *,
        config: Callable[[], AppConfig],
        on_change: Callable[[], Awaitable[None]],
        hardware: Callable[[], HardwareInfo] = detect,
    ) -> None:
        self._bus = bus
        self._config = config
        self._on_change = on_change
        self._detect = hardware
        self._hardware: HardwareInfo | None = None
        self.catalog = catalog
        self.runtime = LlamaRuntime(root, log_dir, catalog)
        self._plan: _Plan | None = None
        self._job: asyncio.Task[None] | None = None
        self._stop = threading.Event()
        self.downloads: dict[str, dict[str, Any]] = {}

    # Equipo ---------------------------------------------------------------------------------------

    async def hardware(self) -> HardwareInfo:
        if self._hardware is None:
            self._hardware = await asyncio.to_thread(self._detect)
        return self._hardware

    async def describe_hardware(self) -> dict[str, Any]:
        info = await self.hardware()
        proposal = propose_tier(info)
        return {**info.to_dict(), "tier": proposal.tier, "reason": proposal.reason}

    async def variant(self) -> RuntimeVariant | None:
        return self.catalog.runtime.choose(await self.hardware(), self._config().runtime.variant)

    # Estado ---------------------------------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        variant = await self.variant()
        installed = await asyncio.to_thread(lambda: {m.id for m in self.runtime.installed_models()})
        return {
            **self.runtime.status(),
            "variant": variant.id if variant else None,
            "variants": [v.id for v in self.catalog.runtime.variants],
            "installed": bool(variant and self.runtime.server_binary(variant)),
            "runtime_size": variant.size if variant else 0,
            "models": [
                {
                    "id": m.id,
                    "name": m.name,
                    "license": m.license,
                    "roles": m.roles,
                    "size": m.size,
                    "vision": m.vision,
                    "description": m.description,
                    "installed": m.id in installed,
                }
                for m in self.catalog.models
            ],
            "running_models": self.runtime.status()["models"],
            "tiers": [t.model_dump() for t in self.catalog.tiers],
            "downloads": list(self.downloads.values()),
            "busy": self.busy,
        }

    @property
    def busy(self) -> bool:
        return self._job is not None and not self._job.done()

    @property
    def endpoint(self) -> tuple[str, str] | None:
        server = self.runtime.server
        return (server.base_url, server.key) if server else None

    # Descargas ------------------------------------------------------------------------------------

    def _publish(self, item: str, **fields: Any) -> None:
        entry = self.downloads.setdefault(item, {"item": item, "label": item, "received": 0, "total": 0})
        entry.update(fields)
        self._bus.publish(DownloadProgress(**entry))

    async def install(self, model_ids: list[str], runtime: bool = True) -> None:
        if self.busy:
            raise EngineError("Ya hay una descarga en marcha")
        entries = [m for i in dict.fromkeys(model_ids) if (m := self.catalog.model(i)) is not None]
        if len(entries) != len(set(model_ids)):
            raise EngineError("Hay modelos que no están en el catálogo")
        variant = await self.variant()
        if runtime and variant is None:
            raise EngineError("No hay motor local para este sistema")
        self.downloads = {}
        if runtime and variant is not None and self.runtime.server_binary(variant) is None:
            self._publish("runtime", label=f"llama.cpp {self.catalog.runtime.tag}", total=variant.size, state="queued")
        else:
            variant = None
        for entry in entries:
            if not self.runtime.model_installed(entry):
                self._publish(entry.id, label=entry.name, total=entry.size, state="queued")
        needed = sum(int(d["total"]) for d in self.downloads.values())
        self.runtime.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.runtime.root).free
        if needed and free < needed * 1.05:
            self.downloads = {}
            raise EngineError(
                f"No hay espacio suficiente: hacen falta {needed / 1e9:.1f} GB y quedan {free / 1e9:.1f} GB libres"
            )
        self._stop.clear()
        self._job = asyncio.create_task(self._run_job(variant, entries), name="engine-install")

    def cancel(self) -> None:
        self._stop.set()

    async def wait(self) -> None:
        if self._job is not None:
            await self._job

    async def _run_job(self, variant: RuntimeVariant | None, entries: list[ModelEntry]) -> None:
        loop = asyncio.get_running_loop()

        def reporter(item: str) -> Callable[[int, int], None]:
            last = [0.0]

            def report(received: int, total: int) -> None:
                now = time.monotonic()
                if now - last[0] >= PROGRESS_INTERVAL_S or received >= total:
                    last[0] = now
                    state = "verifying" if received >= total else "running"
                    loop.call_soon_threadsafe(lambda: self._publish(item, received=received, state=state))

            return report

        steps: list[tuple[str, Callable[[], object]]] = []
        if variant is not None:
            steps.append(("runtime", lambda: self.runtime.install_runtime(variant, reporter("runtime"), self._stop)))
        for entry in entries:
            if entry.id in self.downloads:
                steps.append(
                    (entry.id, lambda e=entry: self.runtime.install_model(e, reporter(e.id), self._stop))  # type: ignore[misc]
                )
        for item, step in steps:
            if self._stop.is_set():
                self._publish(item, state="cancelled")
                continue
            self._publish(item, state="running")
            try:
                await asyncio.to_thread(step)
            except DownloadCancelled:
                self._publish(item, state="cancelled")
                continue
            except Exception as exc:
                log.warning("Falló la descarga de %s: %s", item, exc)
                self._publish(item, state="error", error=str(exc))
                continue
            entry_total = self.downloads[item]["total"]
            self._publish(item, state="done", received=entry_total)
        await self.sync(force=True)

    async def remove(self, model_id: str) -> None:
        entry = self.catalog.model(model_id)
        if entry is None:
            raise EngineError("Ese modelo no está en el catálogo")
        if self.busy:
            raise EngineError("Espera a que terminen las descargas")
        if model_id in self.runtime.status()["models"]:
            await self.runtime.stop()
            self._plan = None
        await asyncio.to_thread(self.runtime.remove_model, entry)
        await self.sync(force=True)

    # Servidor -------------------------------------------------------------------------------------

    async def _desired(self) -> _Plan | None:
        config = self._config()
        if config.provider.kind != "managed":
            return None
        variant = await self.variant()
        if variant is None or self.runtime.server_binary(variant) is None:
            return None
        installed = await asyncio.to_thread(self.runtime.installed_models)
        if not installed:
            return None
        ids = tuple(m.id for m in installed)
        preload = config.provider.model if config.provider.model in ids else None
        return _Plan(variant.id, ids, config.runtime.context, config.runtime.models_max, preload)

    async def sync(self, force: bool = False) -> None:
        """Arranca, reinicia o para el servidor para que case con la configuración."""
        plan = await self._desired()
        if plan is None:
            was_running = self.runtime.state in ("running", "starting", "error")
            if was_running:
                await self.runtime.stop()
            self._plan = None
            if self._config().provider.kind == "managed":
                detail = "Instala el motor y un modelo en la página Modelos"
                self._bus.publish(StatusChanged(component="runtime", state="waiting", detail=detail))
            else:
                self._bus.publish(StatusChanged(component="runtime", state="idle"))
            if was_running:
                await self._on_change()
            return
        if plan == self._plan and self.runtime.state == "running" and not force:
            return
        variant = self.catalog.runtime.variant(plan.variant)
        models = [m for i in plan.models if (m := self.catalog.model(i)) is not None]
        self._bus.publish(StatusChanged(component="runtime", state="starting", detail="Arrancando el motor local…"))
        try:
            assert variant is not None
            await self.runtime.start(
                variant, models, context=plan.context, models_max=plan.models_max, preload=plan.preload
            )
            self._plan = plan
            self._bus.publish(
                StatusChanged(component="runtime", state="running", detail=f"{len(models)} modelos disponibles")
            )
        except EngineError as exc:
            self._plan = None
            self._bus.publish(StatusChanged(component="runtime", state="error", detail=str(exc)[:500]))
        await self._on_change()

    async def stop(self) -> None:
        self.cancel()
        if self._job is not None:
            self._job.cancel()
        await self.runtime.stop()
        self._plan = None
