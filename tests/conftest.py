from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from gamepal.capture.base import RGBImage, WindowInfo
from gamepal.events import TextLine
from gamepal.paths import AppPaths

GAME = WindowInfo(id=7, title="Mi Juego", process="MiJuego.exe", pid=4242, width=800, height=600)


def solid(color: tuple[int, int, int], width: int = 320, height: int = 180) -> RGBImage:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = color
    return image


def pattern(seed: int, width: int = 320, height: int = 180) -> RGBImage:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (height, width, 3), dtype=np.uint8)


class FakeBackend:
    name = "fake"

    def __init__(self, frames: list[RGBImage] | None = None, foreground: int | None = GAME.pid) -> None:
        self.frames = frames or [pattern(1)]
        self.index = 0
        self.foreground = foreground
        self.windows = [GAME]

    def list_windows(self) -> list[WindowInfo]:
        return list(self.windows)

    def foreground_pid(self) -> int | None:
        return self.foreground

    def grab(self, window: WindowInfo) -> RGBImage | None:
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return frame


class FakeOcr:
    name = "fake-ocr"

    def __init__(self, reader: Callable[[RGBImage], list[TextLine]] | None = None) -> None:
        self.calls = 0
        self._reader = reader

    def read(self, image: RGBImage) -> list[TextLine]:
        self.calls += 1
        return self._reader(image) if self._reader else []


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths(tmp_path / "config", tmp_path / "data", tmp_path / "cache", tmp_path / "logs")
    paths.ensure()
    return paths


@pytest.fixture
def repo_profiles() -> Path:
    return Path(__file__).resolve().parent.parent / "profiles"
