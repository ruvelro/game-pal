"""Interpreta cada lectura de pantalla: clasifica, extrae, confirma y, si hace falta, pregunta a la visión."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

from gamepal.bus import EventBus
from gamepal.capture.base import Frame
from gamepal.events import ScreenDetected, ScreenRead
from gamepal.perception.classifier import Classification, ScreenTracker, classify
from gamepal.perception.vision import VisionReader
from gamepal.perception.vocabulary import Vocabulary
from gamepal.profiles.model import GameProfile, Screen

log = logging.getLogger(__name__)


class ScreenInterpreter:
    def __init__(
        self,
        bus: EventBus,
        profile: GameProfile,
        vocabulary: Vocabulary,
        language: str,
        frames: Callable[[int], Frame | None],
        request_read: Callable[[], None],
        vision: VisionReader | None = None,
    ) -> None:
        self._bus = bus
        self.profile = profile
        self.vocabulary = vocabulary
        self.language = language
        self._frames = frames
        self._request_read = request_read
        self._vision = vision
        self.tracker = ScreenTracker()
        self.last: Classification | None = None
        self.last_read: ScreenRead | None = None

    async def run(self) -> None:
        if not self.profile.screens:
            return
        async with self._bus.subscribe({"perception.text"}, maxsize=4) as sub:
            async for event in sub:
                if isinstance(event, ScreenRead):
                    try:
                        await self.handle(event)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("Error interpretando la pantalla")

    async def handle(self, read: ScreenRead) -> Classification:
        self.last_read = read
        frame = self._frames(read.frame_id)
        image = frame.image if frame is not None else None
        result = await asyncio.to_thread(classify, self.profile, self.vocabulary, read.lines, self.language, image)
        source: Literal["signals", "vision"] = "signals"
        if self._vision is not None and image is not None:
            wanted = self._vision_targets(result)
            if wanted:
                seen = await self._vision.read(image, read.lines, wanted, self.language)
                if seen is not None and seen.screen is not None:
                    seen.candidates = result.candidates
                    result, source = seen, "vision"
        self.last = result
        for transition in self.tracker.feed(result):
            if transition.kind == "leave" and self.tracker.current is not None:
                continue  # se anuncia directamente la pantalla nueva
            if transition.kind == "leave":
                self._bus.publish(ScreenDetected(screen_id=None, change="leave", frame_id=read.frame_id))
            else:
                c = transition.classification
                self._bus.publish(
                    ScreenDetected(
                        screen_id=transition.screen.id,
                        change="update" if transition.kind == "update" else "enter",
                        name=transition.screen.description or transition.screen.id,
                        confidence=round(c.confidence, 3),
                        certain=c.certain,
                        complete=c.complete,
                        source=source,
                        data=c.data,
                        frame_id=read.frame_id,
                    )
                )
        if self.tracker.wants_reread:
            self._request_read()
        return result

    def _vision_targets(self, result: Classification) -> list[Screen]:
        always = [s for s in self.profile.screens if s.vision == "always"]
        if result.screen is None:
            return []
        if result.screen.vision == "always" or (
            result.screen.vision == "on_uncertain" and not (result.certain and result.complete)
        ):
            plausible = [c.screen for c in result.candidates if c.matched and c.screen.vision != "never"]
            return list(dict.fromkeys([*plausible, *always]))
        return []
