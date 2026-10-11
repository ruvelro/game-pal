"""Bus de eventos asíncrono en proceso.

Cada suscriptor tiene su propia cola acotada. Si un suscriptor lento llena su cola,
se descartan sus eventos más antiguos: nunca bloquea a quien publica.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from gamepal.events import Event

log = logging.getLogger(__name__)


class Subscription:
    def __init__(self, topics: frozenset[str] | None, maxsize: int) -> None:
        self._topics = topics
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def wants(self, event: Event) -> bool:
        if self._topics is None:
            return True
        return any(event.topic == t or event.topic.startswith(t + ".") for t in self._topics)

    def offer(self, event: Event) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped += 1
        self._queue.put_nowait(event)

    async def get(self) -> Event:
        return await self._queue.get()

    def __aiter__(self) -> AsyncIterator[Event]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Event]:
        while True:
            yield await self._queue.get()


class EventBus:
    def __init__(self) -> None:
        self._subscriptions: set[Subscription] = set()
        self._last: dict[str, Event] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def publish(self, event: Event) -> None:
        """Publica sin bloquear. Desde otro hilo (una descarga, por ejemplo), lo pasa al del bucle de eventos."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._deliver, event)
                return
        self._deliver(event)

    def _deliver(self, event: Event) -> None:
        self._last[self._key(event)] = event
        for sub in tuple(self._subscriptions):
            if sub.wants(event):
                sub.offer(event)

    def last_events(self) -> list[Event]:
        """Último evento de cada tipo, para que un cliente nuevo reciba el estado actual."""
        return list(self._last.values())

    @asynccontextmanager
    async def subscribe(self, topics: set[str] | None = None, maxsize: int = 256) -> AsyncIterator[Subscription]:
        self._loop = asyncio.get_running_loop()
        sub = Subscription(frozenset(topics) if topics else None, maxsize)
        self._subscriptions.add(sub)
        try:
            yield sub
        finally:
            self._subscriptions.discard(sub)
            if sub.dropped:
                log.debug("Suscriptor cerrado con %d eventos descartados", sub.dropped)

    @staticmethod
    def _key(event: Event) -> str:
        component = getattr(event, "component", None)
        return f"{event.topic}:{component}" if component else event.topic
