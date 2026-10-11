"""Arranca y vigila el proceso del overlay."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from gamepal.bus import EventBus
from gamepal.events import StatusChanged
from gamepal.paths import module_command

log = logging.getLogger(__name__)

MAX_RESTARTS = 3
RESTART_WINDOW_S = 60.0


class OverlayProcess:
    def __init__(self, bus: EventBus, port: int, token: str) -> None:
        self._bus = bus
        self._port = port
        self._token = token
        self._task: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._supervise(), name="overlay")

    async def _supervise(self) -> None:
        loop = asyncio.get_running_loop()
        failures: list[float] = []
        while True:
            env = {
                **os.environ,
                "GAMEPAL_HTTP": f"http://127.0.0.1:{self._port}",
                "GAMEPAL_WS": f"ws://127.0.0.1:{self._port}/api/events",
                # El token va por el entorno, nunca por la línea de órdenes (visible para otros procesos).
                "GAMEPAL_TOKEN": self._token,
            }
            self._process = await asyncio.create_subprocess_exec(
                *module_command("gamepal.overlay"),
                env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
            self._bus.publish(StatusChanged(component="overlay", state="running", detail="Overlay activo"))
            _, stderr = await self._process.communicate()
            code = self._process.returncode
            message = stderr.decode(errors="replace").strip().splitlines()[-1:] if stderr else []
            detail = message[0] if message else f"código {code}"
            if code in (2, 3):
                self._bus.publish(StatusChanged(component="overlay", state="error", detail=detail))
                return
            now = loop.time()
            failures = [t for t in failures if now - t < RESTART_WINDOW_S] + [now]
            if len(failures) > MAX_RESTARTS:
                self._bus.publish(StatusChanged(component="overlay", state="error", detail=f"Se cerró: {detail}"))
                return
            log.warning("El overlay se cerró (%s); se reinicia", detail)
            await asyncio.sleep(1)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        process = self._process
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._bus.publish(StatusChanged(component="overlay", state="idle", detail="Overlay apagado"))
