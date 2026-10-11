"""Síntesis de voz: Piper en local o la voz del sistema. Y limpieza del texto antes de hablarlo."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

log = logging.getLogger(__name__)

_URL = re.compile(r"https?://\S+|www\.\S+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKUP = re.compile(r"[*_`#>|~]+")
_CITATION = re.compile(r"\[\d+\]|【[^】]*】|\[\^[^\]]+\]")
_SPACES = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"([.!?…]+[\"')\]]?)(\s+|$)")
MAX_CHUNK = 220


def speakable(text: str) -> str:
    """Quita lo que no debe leerse en voz alta: enlaces, citas y marcas de formato."""
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _CITATION.sub("", text)
    text = _MARKUP.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


class SentenceBuffer:
    """Acumula texto en streaming y entrega frases completas para empezar a hablar cuanto antes."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        out: list[str] = []
        while True:
            match = _SENTENCE_END.search(self._buffer)
            if match is None or (match.end() == len(self._buffer) and not match.group(2)):
                break
            sentence, self._buffer = self._buffer[: match.end()], self._buffer[match.end() :]
            out.extend(self._clean(sentence))
        if len(self._buffer) > MAX_CHUNK:
            cut = self._buffer.rfind(" ", 0, MAX_CHUNK)
            cut = cut if cut > 40 else MAX_CHUNK
            out.extend(self._clean(self._buffer[:cut]))
            self._buffer = self._buffer[cut:]
        return out

    def flush(self) -> list[str]:
        rest, self._buffer = self._buffer, ""
        return self._clean(rest)

    @staticmethod
    def _clean(text: str) -> list[str]:
        cleaned = speakable(text)
        return [cleaned] if any(ch.isalnum() for ch in cleaned) else []


Pcm = npt.NDArray[np.float32]


class Tts(Protocol):
    name: str

    def synthesize(self, text: str) -> tuple[Pcm, int] | None:
        """Audio mono en float32 y su frecuencia. None si el motor habla por su cuenta (voz del sistema)."""
        ...

    def speak_blocking(self, text: str) -> None: ...

    def stop(self) -> None: ...


class PiperTts:
    def __init__(self, model_path: Path, rate: float = 1.0) -> None:
        from piper import PiperVoice, SynthesisConfig

        self.name = f"piper-{model_path.stem}"
        self._voice: Any = PiperVoice.load(str(model_path))
        self._config: Any = SynthesisConfig(length_scale=1.0 / rate)
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> tuple[Pcm, int] | None:
        with self._lock:
            chunks = list(self._voice.synthesize(text, syn_config=self._config))
        if not chunks:
            return np.zeros(0, dtype=np.float32), 22050
        audio = np.concatenate([chunk.audio_float_array for chunk in chunks]).astype(np.float32)
        return audio, int(chunks[0].sample_rate)

    def speak_blocking(self, text: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        return None


class SystemTts:
    """La voz que ya trae el sistema. Sin descargas, pero con menos control."""

    name = "system"

    def __init__(self, language: str, rate: float = 1.0) -> None:
        self._language = language
        self._rate = rate
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._command = self._build_command()
        if self._command is None:
            raise RuntimeError("No hay voz del sistema disponible (instala espeak-ng o speech-dispatcher en Linux)")

    def _build_command(self) -> list[str] | None:
        if sys.platform == "darwin":
            return ["say", "-r", str(round(185 * self._rate)), "-f", "-"]
        if sys.platform == "win32":
            rate = max(-10, min(10, round((self._rate - 1) * 10)))
            script = (
                "Add-Type -AssemblyName System.Speech;"
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                f"$s.Rate={rate};$s.Speak([Console]::In.ReadToEnd())"
            )
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
        for tool in ("spd-say", "espeak-ng", "espeak"):
            path = shutil.which(tool)
            if path:
                if tool == "spd-say":
                    return [path, "-w", "-l", self._language, "-e"]
                return [path, "-v", self._language, "--stdin"]
        return None

    def synthesize(self, text: str) -> tuple[Pcm, int] | None:
        return None

    def speak_blocking(self, text: str) -> None:
        assert self._command is not None
        with self._lock:
            # El texto va por la entrada estándar: nunca se interpreta como argumento ni como código.
            self._process = subprocess.Popen(  # noqa: S603
                self._command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        process = self._process
        try:
            process.communicate(text.encode("utf-8"), timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()

    def stop(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
