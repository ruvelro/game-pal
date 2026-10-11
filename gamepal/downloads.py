"""Progreso de descargas que no lo informan por sí solas (Whisper, el modelo de búsqueda…).

Mientras una descarga bloqueante escribe en una carpeta, un hilo mide cuánto crece y lo comunica.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

MB = 1024 * 1024
# Por debajo de esto no se avisa: es leer un modelo ya descargado, no descargarlo.
MIN_REPORT_BYTES = 2 * MB

Report = Callable[[int, float | None], None]


def folder_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def describe(name: str, received: int, expected_mb: float | None) -> str:
    done = received / MB
    if expected_mb:
        return f"Descargando {name}: {done:.0f} de {expected_mb:.0f} MB"
    return f"Descargando {name}: {done:.0f} MB"


class FolderProgress:
    """`with FolderProgress(carpeta, informe, mb_esperados): descarga()` llama a informe(bytes, fracción)."""

    def __init__(self, folder: Path, report: Report, expected_mb: float | None = None, interval: float = 0.5) -> None:
        self._folder = folder
        self._report = report
        self._expected = expected_mb * MB if expected_mb else None
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="download-progress", daemon=True)
        self._start = 0
        self.downloaded = 0

    def _measure(self) -> None:
        grown = folder_size(self._folder) - self._start if self._folder.is_dir() else 0
        if grown >= MIN_REPORT_BYTES and grown != self.downloaded:
            self.downloaded = grown
            fraction = min(grown / self._expected, 0.99) if self._expected else None
            self._report(grown, fraction)

    def _watch(self) -> None:
        while not self._stop.wait(self._interval):
            self._measure()

    def __enter__(self) -> FolderProgress:
        self._start = folder_size(self._folder) if self._folder.is_dir() else 0
        self._thread.start()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._stop.set()
        self._thread.join()
