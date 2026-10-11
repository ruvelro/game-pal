"""Bucle de observación: localiza la ventana, captura, detecta cambios y lee el texto.

Frecuencia adaptativa: idle_fps en reposo, active_fps mientras la pantalla cambia y
pausa total cuando el juego no está en primer plano. El OCR solo se lanza cuando una
pantalla nueva se ha estabilizado, y nunca hay dos lecturas a la vez.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from gamepal.bus import EventBus
from gamepal.capture import (
    CaptureBackend,
    CaptureUnavailableError,
    Frame,
    WindowInfo,
    downscale,
    match_window,
)
from gamepal.config import CaptureConfig
from gamepal.events import FrameCaptured, ScreenRead, StatusChanged, WindowSeen
from gamepal.perception.ocr import OcrEngine
from gamepal.perception.phash import dhash, distance

log = logging.getLogger(__name__)

WINDOW_REFRESH_S = 3.0
ACTIVE_HOLD_S = 2.0
BACKGROUND_POLL_S = 0.75


@dataclass(frozen=True, slots=True)
class Target:
    processes: tuple[str, ...]
    title: str | None

    @property
    def defined(self) -> bool:
        return bool(self.processes) or bool(self.title)


class ScreenWatcher:
    def __init__(
        self,
        bus: EventBus,
        backend: CaptureBackend,
        ocr: OcrEngine,
        config: CaptureConfig,
        target: Target,
        *,
        paused: bool = False,
    ) -> None:
        self._bus = bus
        self._backend = backend
        self._ocr = ocr
        self._config = config
        self._target = target
        self._window: WindowInfo | None = None
        self._window_checked = 0.0
        self._frame_id = 0
        self._previous_hash: int | None = None
        self._read_hash: int | None = None
        self._stable = 0
        self._last_change = 0.0
        self._ocr_task: asyncio.Task[None] | None = None
        self._force_read = False
        self._read_frames: dict[int, Frame] = {}
        self._running = asyncio.Event()
        if not paused:
            self._running.set()
        self.latest_frame: Frame | None = None

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def resume(self) -> None:
        self._running.set()

    def pause(self) -> None:
        self._running.clear()
        self._bus.publish(StatusChanged(component="capture", state="paused", detail="Observación en pausa"))

    def request_read(self) -> None:
        """Pide leer otra vez la pantalla en cuanto esté estable, aunque no haya cambiado."""
        self._force_read = True

    def frame_for_read(self, frame_id: int) -> Frame | None:
        return self._read_frames.get(frame_id)

    def _status(self, state: str, detail: str) -> None:
        self._bus.publish(StatusChanged(component="capture", state=state, detail=detail))  # type: ignore[arg-type]

    async def _locate(self, force: bool = False) -> WindowInfo | None:
        now = time.monotonic()
        if not force and self._window is not None and now - self._window_checked < WINDOW_REFRESH_S:
            return self._window
        self._window_checked = now
        windows = await asyncio.to_thread(self._backend.list_windows)
        foreground = await asyncio.to_thread(self._backend.foreground_pid)
        window = match_window(windows, list(self._target.processes), self._target.title, foreground)
        if window != self._window:
            self._window = window
            self._previous_hash = self._read_hash = None
            if window is not None:
                log.info(
                    "Ventana del juego: %s (%s, %dx%d)",
                    window.title,
                    window.process,
                    window.width,
                    window.height,
                )
        return window

    async def run(self) -> None:
        while True:
            await self._running.wait()
            try:
                delay = await self._tick()
            except CaptureUnavailableError as exc:
                self._status("error", str(exc))
                delay = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Error en el bucle de captura")
                self._status("error", f"Error de captura: {exc}")
                delay = 2.0
            await asyncio.sleep(delay)

    async def _tick(self) -> float:
        if not self._target.defined:
            self._status("waiting", "Elige la ventana del juego en la GUI")
            return 1.0
        window = await self._locate()
        if window is None:
            self._status("waiting", "Buscando la ventana del juego…")
            return 1.0

        foreground = await asyncio.to_thread(self._backend.foreground_pid)
        is_front = foreground == window.pid
        if self._config.require_foreground and not is_front:
            self._bus.publish(
                WindowSeen(
                    title=window.title, process=window.process, width=window.width,
                    height=window.height, foreground=False,
                    left=window.left, top=window.top,
                )
            )  # fmt: skip
            self._status("waiting", "El juego no está en primer plano")
            return BACKGROUND_POLL_S

        raw = await asyncio.to_thread(self._backend.grab, window)
        if raw is None:
            await self._locate(force=True)
            return 0.5
        image = downscale(raw, self._config.max_width)
        self._frame_id += 1
        frame = Frame(self._frame_id, image, window)
        self.latest_frame = frame

        current = await asyncio.to_thread(dhash, image)
        moved = 0 if self._previous_hash is None else distance(current, self._previous_hash)
        self._previous_hash = current
        changed = moved >= self._config.change_threshold
        now = time.monotonic()
        if changed:
            self._stable = 0
            self._last_change = now
        else:
            self._stable += 1

        new_screen = self._read_hash is None or distance(current, self._read_hash) >= self._config.change_threshold
        ocr_idle = self._ocr_task is None or self._ocr_task.done()
        wants_read = new_screen or self._force_read
        if wants_read and self._stable >= self._config.settle_frames and ocr_idle:
            self._read_hash = current
            self._force_read = False
            # Se guardan los frames leídos recientes para que la visión use exactamente esa imagen.
            self._read_frames[frame.id] = frame
            for old in sorted(self._read_frames)[:-3]:
                del self._read_frames[old]
            self._ocr_task = asyncio.create_task(self._read(frame))

        self._bus.publish(
            WindowSeen(
                title=window.title, process=window.process, width=window.width,
                height=window.height, foreground=is_front,
                left=window.left, top=window.top,
            )
        )  # fmt: skip
        self._bus.publish(
            FrameCaptured(frame_id=frame.id, width=frame.width, height=frame.height, distance=moved, changed=changed)
        )
        self._status("running", f"Observando {window.title or window.process}")
        active = now - self._last_change < ACTIVE_HOLD_S
        return 1.0 / (self._config.active_fps if active else self._config.idle_fps)

    async def _read(self, frame: Frame) -> None:
        started = time.perf_counter()
        try:
            lines = await asyncio.to_thread(self._ocr.read, frame.image)
        except Exception as exc:
            log.exception("Error de OCR")
            self._bus.publish(StatusChanged(component="ocr", state="error", detail=str(exc)))
            return
        elapsed = (time.perf_counter() - started) * 1000
        self._bus.publish(StatusChanged(component="ocr", state="ready", detail=f"{len(lines)} líneas"))
        self._bus.publish(ScreenRead(frame_id=frame.id, backend=self._ocr.name, elapsed_ms=elapsed, lines=lines))
