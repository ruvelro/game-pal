"""Transcripción de voz: faster-whisper en local o un servidor local compatible con OpenAI."""

from __future__ import annotations

import io
import logging
import sys
import threading
import types
import wave
from typing import Any, Protocol

import httpx
import numpy as np

from gamepal.security import validate_local_endpoint
from gamepal.voice.vad import SAMPLE_RATE, Audio

log = logging.getLogger(__name__)

MAX_HOTWORDS_CHARS = 400


class Stt(Protocol):
    name: str

    def transcribe(self, audio: Audio, language: str | None, hotwords: str | None = None) -> str: ...


def _without_pyav() -> None:
    """faster-whisper importa PyAV solo para leer ficheros de audio; game-pal le da el audio ya en memoria.

    PyAV trae su propia copia de FFmpeg, igual que OpenCV (el OCR). Con las dos cargadas, macOS avisa de clases
    Objective-C duplicadas (AVFFrameReceiver…) que pueden causar fallos raros: así solo se carga la de OpenCV.
    """
    if "av" in sys.modules:
        return
    stub = types.ModuleType("av")

    def missing(name: str) -> Any:
        raise AttributeError(f"game-pal no carga PyAV (se pidió av.{name}): el audio va en memoria")

    stub.__getattr__ = missing  # type: ignore[method-assign]
    sys.modules["av"] = stub


class WhisperStt:
    """faster-whisper en CPU (int8) o GPU. El modelo se descarga de Hugging Face la primera vez."""

    def __init__(self, model: str, download_root: str | None = None) -> None:
        _without_pyav()
        from faster_whisper import WhisperModel

        self.name = f"whisper-{model}"
        self._model: Any = WhisperModel(model, device="auto", compute_type="int8", download_root=download_root)
        self._lock = threading.Lock()

    def transcribe(self, audio: Audio, language: str | None, hotwords: str | None = None) -> str:
        if len(audio) < SAMPLE_RATE // 4:
            return ""
        with self._lock:
            segments, _ = self._model.transcribe(
                audio,
                language=language or None,
                beam_size=1,
                condition_on_previous_text=False,
                vad_filter=False,
                # Sin reintentos a otras temperaturas: cuando duda, Whisper repetía la frase hasta seis veces
                # (una frase de 2 s llegaba a tardar 9 s) y no acertaba más. Sin marcas de tiempo, va más rápido.
                temperature=0.0,
                without_timestamps=True,
                # Los nombres del juego ayudan a Whisper a escribirlos bien.
                hotwords=(hotwords or None) and hotwords[:MAX_HOTWORDS_CHARS],
            )
            return " ".join(segment.text.strip() for segment in segments).strip()


def to_wav(audio: Audio) -> bytes:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    return buffer.getvalue()


class LocalServerStt:
    """Servidor en este equipo con la API /audio/transcriptions (whisper.cpp, speaches, LM Studio…)."""

    def __init__(self, base_url: str, model: str) -> None:
        self.name = f"server-{model}"
        self._url = validate_local_endpoint(base_url) + "/audio/transcriptions"
        self._model = model

    def transcribe(self, audio: Audio, language: str | None, hotwords: str | None = None) -> str:
        data = {"model": self._model, "response_format": "json"}
        if language:
            data["language"] = language
        if hotwords:
            data["prompt"] = hotwords[:MAX_HOTWORDS_CHARS]
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            response = client.post(self._url, data=data, files={"file": ("audio.wav", to_wav(audio), "audio/wav")})
        if response.status_code >= 400:
            raise RuntimeError(f"El servidor de transcripción respondió {response.status_code}")
        body = response.json()
        return str(body.get("text", "")).strip() if isinstance(body, dict) else ""
