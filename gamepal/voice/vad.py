"""Detección de voz (Silero en ONNX) y segmentación en frases."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16_000
FRAME = 512  # 32 ms: el tamaño que espera Silero a 16 kHz
FRAME_SECONDS = FRAME / SAMPLE_RATE

Audio = npt.NDArray[np.float32]


class Vad(Protocol):
    def probability(self, frame: Audio) -> float: ...

    def reset(self) -> None: ...


class SileroVad:
    """Silero VAD v5. Mantiene estado entre frames, así que cada flujo de audio necesita su instancia."""

    CONTEXT = 64

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._session: Any = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.CONTEXT), dtype=np.float32)

    def probability(self, frame: Audio) -> float:
        if frame.shape != (FRAME,):
            raise ValueError(f"Silero espera frames de {FRAME} muestras")
        x = np.concatenate([self._context, frame.reshape(1, -1)], axis=1)
        out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -self.CONTEXT :]
        return float(out[0][0])


class EnergyVad:
    """Respaldo sin modelo: energía frente a un suelo de ruido que se adapta solo."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._noise = 0.003

    def probability(self, frame: Audio) -> float:
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        ratio = rms / max(self._noise, 1e-5)
        if ratio < 2:
            self._noise = 0.95 * self._noise + 0.05 * rms
        return float(np.clip((ratio - 1.5) / 3.0, 0.0, 1.0))


@dataclass(frozen=True, slots=True)
class Utterance:
    audio: Audio
    forced: bool = False  # cerrada por push-to-talk, no por silencio

    @property
    def seconds(self) -> float:
        return len(self.audio) / SAMPLE_RATE


class Segmenter:
    """Convierte probabilidades de voz por frame en frases completas.

    Arranca con `threshold`, termina tras `hangover` segundos por debajo de `threshold - 0.15`
    e incluye un poco de audio previo para no cortar la primera sílaba.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        min_speech: float = 0.25,
        hangover: float = 0.7,
        pre_roll: float = 0.3,
        max_seconds: float = 15.0,
    ) -> None:
        self.threshold = threshold
        self._min_frames = max(1, round(min_speech / FRAME_SECONDS))
        self._hang_frames = max(1, round(hangover / FRAME_SECONDS))
        self._max_frames = round(max_seconds / FRAME_SECONDS)
        # +1: el frame que dispara la detección también entra en la cola previa.
        self._pre: deque[Audio] = deque(maxlen=max(1, round(pre_roll / FRAME_SECONDS)) + 1)
        self._frames: list[Audio] = []
        self._voiced = 0
        self._silence = 0
        self.active = False
        self.forced = False
        # Cuenta las frases: sirve para saber si una comprobación hecha al vuelo es de la frase en curso.
        self.segment = 0

    def reset(self) -> None:
        self._pre.clear()
        self._frames.clear()
        self._voiced = self._silence = 0
        self.active = self.forced = False

    @property
    def speech_seconds(self) -> float:
        return self._voiced * FRAME_SECONDS

    @property
    def silence_seconds(self) -> float:
        return self._silence * FRAME_SECONDS

    def snapshot(self, max_seconds: float | None = None) -> Audio:
        """Lo grabado hasta ahora en la frase en curso (o su principio), sin cerrarla."""
        frames = self._frames
        if max_seconds is not None:
            frames = frames[: max(1, round(max_seconds / FRAME_SECONDS))]
        return np.concatenate(frames).astype(np.float32) if frames else np.zeros(0, dtype=np.float32)

    def force_start(self) -> None:
        """Push-to-talk: empieza a grabar ya, haya voz o no."""
        self._frames = list(self._pre)
        self._voiced = self._silence = 0
        self.active = self.forced = True
        self.segment += 1

    def force_end(self) -> Utterance | None:
        if not self.active:
            return None
        audio = np.concatenate(self._frames) if self._frames else np.zeros(0, dtype=np.float32)
        self.reset()
        return Utterance(audio.astype(np.float32), forced=True) if len(audio) else None

    def feed(self, frame: Audio, probability: float) -> Utterance | None:
        if not self.active:
            self._pre.append(frame)
            if probability >= self.threshold:
                self.active = True
                self.segment += 1
                self._frames = list(self._pre)
                self._voiced, self._silence = 1, 0
            return None
        self._frames.append(frame)
        if probability >= self.threshold - 0.15:
            self._voiced += 1
            self._silence = 0
        else:
            self._silence += 1
        too_long = len(self._frames) >= self._max_frames
        ended = not self.forced and self._silence >= self._hang_frames
        if not (ended or too_long):
            return None
        enough = self._voiced >= self._min_frames or self.forced
        frames = self._frames[: len(self._frames) - (self._silence if ended else 0)] or self._frames
        audio = np.concatenate(frames).astype(np.float32)
        forced = self.forced
        self.reset()
        return Utterance(audio, forced=forced) if enough else None
