import asyncio
import threading

from gamepal.bus import EventBus
from gamepal.events import LogMessage, StatusChanged


async def test_subscribers_receive_matching_topics() -> None:
    bus = EventBus()
    async with bus.subscribe({"status"}) as statuses, bus.subscribe() as everything:
        bus.publish(StatusChanged(component="ocr", state="ready"))
        bus.publish(LogMessage(level="info", message="hola"))
        first = await asyncio.wait_for(statuses.get(), 1)
        assert isinstance(first, StatusChanged)
        assert statuses._queue.empty()
        assert {type(await everything.get()) for _ in range(2)} == {StatusChanged, LogMessage}


async def test_slow_subscriber_drops_oldest_without_blocking() -> None:
    bus = EventBus()
    async with bus.subscribe(maxsize=2) as sub:
        for i in range(5):
            bus.publish(LogMessage(level="info", message=str(i)))
        assert sub.dropped == 3
        messages = [await sub.get(), await sub.get()]
        assert [m.message for m in messages if isinstance(m, LogMessage)] == ["3", "4"]


async def test_last_events_keeps_one_status_per_component() -> None:
    bus = EventBus()
    bus.publish(StatusChanged(component="ocr", state="starting"))
    bus.publish(StatusChanged(component="ocr", state="ready"))
    bus.publish(StatusChanged(component="capture", state="running"))
    states = {(e.component, e.state) for e in bus.last_events() if isinstance(e, StatusChanged)}
    assert states == {("ocr", "ready"), ("capture", "running")}


async def test_unsubscribe_on_exit() -> None:
    bus = EventBus()
    async with bus.subscribe():
        assert len(bus._subscriptions) == 1
    assert not bus._subscriptions


async def test_publish_from_another_thread_reaches_subscribers() -> None:
    bus = EventBus()
    async with bus.subscribe({"status"}) as sub:
        thread = threading.Thread(
            target=bus.publish, args=(StatusChanged(component="voice", state="starting", detail="Descargando"),)
        )
        thread.start()
        thread.join()
        event = await asyncio.wait_for(sub.get(), 1)
    assert isinstance(event, StatusChanged)
    assert event.detail == "Descargando"
