import asyncio

from gamepal.bus import EventBus
from gamepal.config import CaptureConfig
from gamepal.events import Box, ScreenRead, StatusChanged, TextLine
from gamepal.perception.watcher import ScreenWatcher, Target
from tests.conftest import FakeBackend, FakeOcr, pattern

FAST = CaptureConfig(idle_fps=10, active_fps=10, settle_frames=1)
TARGET = Target(("mijuego",), None)


async def _run(watcher: ScreenWatcher, ticks: int) -> None:
    for _ in range(ticks):
        await watcher._tick()
        await asyncio.sleep(0.01)
    if watcher._ocr_task:
        await watcher._ocr_task


async def test_reads_each_new_screen_once_after_it_settles() -> None:
    a, b = pattern(1), pattern(2)
    backend = FakeBackend([a, a, a, b, b, b])
    line = TextLine(text="Elige", confidence=1, box=Box(x=0, y=0, width=1, height=1))
    ocr = FakeOcr(lambda image: [line])
    bus = EventBus()
    async with bus.subscribe({"perception"}) as reads:
        watcher = ScreenWatcher(bus, backend, ocr, FAST, TARGET)
        await _run(watcher, 6)
        assert ocr.calls == 2
        event = await reads.get()
        assert isinstance(event, ScreenRead)
        assert event.lines[0].text == "Elige"
    assert watcher.latest_frame is not None


async def test_waits_while_game_is_in_background() -> None:
    backend = FakeBackend([pattern(1)], foreground=999)
    ocr = FakeOcr()
    bus = EventBus()
    watcher = ScreenWatcher(bus, backend, ocr, FAST, TARGET)
    await _run(watcher, 3)
    assert backend.index == 0
    assert ocr.calls == 0
    statuses = [e for e in bus.last_events() if isinstance(e, StatusChanged)]
    assert statuses[-1].detail == "El juego no está en primer plano"


async def test_background_capture_when_allowed() -> None:
    backend = FakeBackend([pattern(1)], foreground=999)
    watcher = ScreenWatcher(
        EventBus(), backend, FakeOcr(), FAST.model_copy(update={"require_foreground": False}), TARGET
    )
    await _run(watcher, 2)
    assert backend.index == 2


async def test_without_target_it_asks_for_a_window() -> None:
    bus = EventBus()
    backend = FakeBackend()
    watcher = ScreenWatcher(bus, backend, FakeOcr(), FAST, Target((), None))
    await watcher._tick()
    assert backend.index == 0
    status = next(e for e in bus.last_events() if isinstance(e, StatusChanged))
    assert status.state == "waiting"


async def test_pause_blocks_the_loop() -> None:
    backend = FakeBackend([pattern(1)])
    watcher = ScreenWatcher(EventBus(), backend, FakeOcr(), FAST, TARGET, paused=True)
    task = asyncio.create_task(watcher.run())
    await asyncio.sleep(0.05)
    assert backend.index == 0
    watcher.resume()
    await asyncio.sleep(0.15)
    assert backend.index > 0
    task.cancel()
