"""Entrada y salida de audio con PortAudio (sounddevice)."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any

import numpy as np

from gamepal.voice.vad import FRAME, SAMPLE_RATE, Audio

log = logging.getLogger(__name__)


def list_devices() -> dict[str, list[str]]:
    import sounddevice as sd

    inputs, outputs = [], []
    for device in sd.query_devices():
        if device["max_input_channels"] > 0:
            inputs.append(str(device["name"]))
        if device["max_output_channels"] > 0:
            outputs.append(str(device["name"]))
    return {"inputs": inputs, "outputs": outputs}


def _device_index(name: str, kind: str) -> int | None:
    if not name:
        return None
    import sounddevice as sd

    for index, device in enumerate(sd.query_devices()):
        channels = device["max_input_channels"] if kind == "input" else device["max_output_channels"]
        if device["name"] == name and channels > 0:
            return index
    log.warning("No existe el dispositivo '%s'; se usa el predeterminado", name)
    return None


class Microphone:
    """Entrega frames de 32 ms a 16 kHz en una cola asyncio. Si el consumidor se retrasa, se descartan."""

    def __init__(self, device: str = "") -> None:
        self._device = device
        self._stream: Any = None
        self.queue: asyncio.Queue[Audio] = asyncio.Queue(maxsize=200)
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        import sounddevice as sd

        self._loop = asyncio.get_running_loop()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=FRAME,
            device=_device_index(self._device, "input"),
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, data: Any, frames: int, time: Any, status: Any) -> None:
        frame = np.array(data[:, 0], dtype=np.float32, copy=True)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._put, frame)

    def _put(self, frame: Audio) -> None:
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(frame)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Speaker:
    """Reproduce trozos de audio en orden y se calla al instante con `stop()`."""

    def __init__(self, device: str = "", volume: float = 0.9) -> None:
        self._device = device
        self.volume = volume
        self._queue: deque[tuple[Audio, int]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        return not self._idle.is_set()

    def play(self, audio: Audio, rate: int) -> None:
        with self._lock:
            self._queue.append((audio, rate))
            self._idle.clear()
        self._wake.set()

    def stop(self) -> None:
        with self._lock:
            self._queue.clear()
        self._stop.set()
        self._wake.set()

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def _run(self) -> None:
        import sounddevice as sd

        while True:
            self._wake.wait()
            self._wake.clear()
            self._stop.clear()
            while True:
                with self._lock:
                    if not self._queue:
                        self._idle.set()
                        break
                    audio, rate = self._queue.popleft()
                try:
                    data = np.clip(audio * self.volume, -1, 1).astype(np.float32)
                    with sd.OutputStream(
                        samplerate=rate, channels=1, dtype="float32", device=_device_index(self._device, "output")
                    ) as stream:
                        block = rate // 20  # 50 ms: permite cortar casi al instante
                        for start in range(0, len(data), block):
                            if self._stop.is_set():
                                break
                            stream.write(data[start : start + block].reshape(-1, 1))
                except Exception:
                    log.exception("No se pudo reproducir audio")
                if self._stop.is_set():
                    with self._lock:
                        self._queue.clear()
                        self._idle.set()
                    break


def tone(rising: bool = True, rate: int = 22050, seconds: float = 0.18) -> tuple[Audio, int]:
    """Pitido corto: ascendente al empezar a escuchar, descendente al cancelar."""
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    start, end = (660.0, 990.0) if rising else (880.0, 550.0)
    freq = np.linspace(start, end, len(t))
    wave = np.sin(2 * np.pi * np.cumsum(freq) / rate)
    envelope = np.minimum(1, np.minimum(t, seconds - t) / 0.02)
    return (0.25 * wave * envelope).astype(np.float32), rate


def measure_input(device: str = "", seconds: float = 3.0) -> dict[str, float | bool]:
    """Graba unos segundos y devuelve el nivel en dBFS, para comprobar el micrófono en el asistente."""
    import sounddevice as sd

    frames = int(SAMPLE_RATE * seconds)
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=_device_index(device, "input"))
    sd.wait()
    samples = np.asarray(audio, dtype=np.float64).reshape(-1)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0

    def db(value: float) -> float:
        return round(20 * float(np.log10(max(value, 1e-6))), 1)

    # Por encima de -45 dBFS de pico hay voz o ruido claro; por debajo, el micrófono no capta nada.
    return {"peak_db": db(peak), "rms_db": db(rms), "heard": db(peak) > -45}


def play_test_tone(device: str = "", volume: float = 0.9) -> None:
    import sounddevice as sd

    up, rate = tone(True)
    down, _ = tone(False)
    sd.play(np.concatenate([up, down]) * volume, rate, device=_device_index(device, "output"))
    sd.wait()
