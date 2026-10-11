"""Voz sintética para probar la entrada de voz sin micrófono."""

from __future__ import annotations

import numpy as np

from gamepal.voice.vad import FRAME, SAMPLE_RATE


def resample(audio: np.ndarray, rate: int) -> np.ndarray:
    if rate == SAMPLE_RATE:
        return audio.astype(np.float32)
    count = int(len(audio) * SAMPLE_RATE / rate)
    return np.interp(np.linspace(0, len(audio) - 1, count), np.arange(len(audio)), audio).astype(np.float32)


def frames(audio: np.ndarray, silence_before: float = 0.6, silence_after: float = 1.5) -> list[np.ndarray]:
    """Trocea en frames de 32 ms con silencio alrededor, como llegaría del micrófono."""
    rng = np.random.default_rng(0)
    pad = lambda seconds: (rng.standard_normal(int(seconds * SAMPLE_RATE)) * 0.002).astype(np.float32)  # noqa: E731
    full = np.concatenate([pad(silence_before), audio, pad(silence_after)])
    usable = len(full) - len(full) % FRAME
    return list(full[:usable].reshape(-1, FRAME))
