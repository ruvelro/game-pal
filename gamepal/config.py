"""Configuración de la aplicación, validada con pydantic y guardada en YAML.

Nunca contiene secretos: las claves de API viven en el almacén de credenciales del sistema.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

OcrBackendName = Literal["rapidocr", "apple-vision", "windows-ocr", "none"]


def rapidocr_available() -> bool:
    """RapidOCR no se instala donde faltan sus ruedas (Windows ARM64)."""
    return importlib.util.find_spec("rapidocr") is not None


def default_ocr_backend() -> OcrBackendName:
    if sys.platform == "win32" and not rapidocr_available():
        return "windows-ocr"
    return "rapidocr"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CaptureConfig(_Model):
    idle_fps: float = Field(default=2.0, gt=0, le=10)
    active_fps: float = Field(default=5.0, gt=0, le=15)
    # Distancia de Hamming (sobre 256 bits) a partir de la cual un frame se considera distinto.
    change_threshold: int = Field(default=12, ge=1, le=128)
    # Frames estables consecutivos antes de leer el texto de una pantalla nueva.
    settle_frames: int = Field(default=1, ge=0, le=10)
    # Ancho máximo al que se reduce el frame antes de procesarlo.
    max_width: int = Field(default=1920, ge=320, le=7680)
    # Si es False, se captura aunque el juego no esté en primer plano.
    require_foreground: bool = True


class OcrConfig(_Model):
    backend: OcrBackendName = Field(default_factory=default_ocr_backend)
    min_confidence: float = Field(default=0.5, ge=0, le=1)


class ProviderConfig(_Model):
    kind: Literal["local-openai"] = "local-openai"
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = ""
    # Modelo con visión para pantallas dudosas. Vacío: la visión queda desactivada.
    vision_model: str = ""
    # Modelo pequeño y rápido para decidir cómo responder y para respuestas breves. Vacío: no se usa.
    fast_model: str = ""
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)


class WakeConfig(_Model):
    enabled: bool = True
    # Frases en cualquier idioma. Se reconocen transcribiendo fragmentos cortos de voz.
    phrases: list[str] = Field(default_factory=lambda: ["oye compañero", "hey pal"], max_length=10)
    # Tras la frase clave, segundos de espera para la pregunta si no se dijo seguida.
    listen_seconds: float = Field(default=7.0, ge=2, le=30)


class PushToTalkConfig(_Model):
    enabled: bool = True
    # Combinación con la sintaxis de pynput: "<ctrl>+<alt>+p", "<f9>"…
    key: str = "<ctrl>+<alt>+p"
    # Botón de mando con nombres de SDL: "back", "leftstick", "rightshoulder"… Vacío: sin mando.
    gamepad_button: str = ""
    mode: Literal["hold", "toggle"] = "hold"


class SttConfig(_Model):
    backend: Literal["faster-whisper", "local-openai"] = "faster-whisper"
    # Modelo de Whisper para las preguntas: tiny, base, small, medium, large-v3-turbo…
    model: str = "base"
    # Modelo para detectar la frase clave. Vacío: el mismo que para las preguntas (ahorra memoria).
    wake_model: str = ""
    # Solo para backend local-openai: servidor compatible con /audio/transcriptions.
    base_url: str = "http://127.0.0.1:8000/v1"


class TtsConfig(_Model):
    backend: Literal["piper", "system", "none"] = "piper"
    # Voz de Piper: id del catálogo (p. ej. es_ES-davefx-medium). Vacío: la recomendada para el idioma.
    voice: str = ""
    volume: float = Field(default=0.9, ge=0, le=1)
    rate: float = Field(default=1.0, ge=0.5, le=2.0)


class VoiceConfig(_Model):
    enabled: bool = False
    input_device: str = ""
    # Idioma en que habla el jugador. Vacío: el idioma de las respuestas.
    language: str = ""
    wake: WakeConfig = Field(default_factory=WakeConfig)
    push_to_talk: PushToTalkConfig = Field(default_factory=PushToTalkConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    # Frases que cortan la respuesta hablada.
    stop_phrases: list[str] = Field(default_factory=lambda: ["gracias", "para", "silencio", "thanks", "stop"])
    # Sensibilidad del detector de voz (probabilidad mínima de habla).
    vad_threshold: float = Field(default=0.5, ge=0.1, le=0.95)


Output = Literal["overlay", "text", "voice"]
Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]


def _answer_outputs() -> list[Output]:
    # La voz se activa a mano: evita descargar modelos de voz sin que el usuario lo pida.
    return ["overlay", "text"]


class OverlayConfig(_Model):
    enabled: bool = True
    corner: Corner = "top-right"
    width: int = Field(default=420, ge=240, le=900)
    opacity: float = Field(default=0.92, ge=0.3, le=1)
    font_scale: float = Field(default=1.0, ge=0.7, le=2)
    # Segundos que sigue visible una tarjeta tras terminar.
    linger_seconds: float = Field(default=12, ge=2, le=120)


class ProactiveConfig(_Model):
    enabled: bool = True
    # Atajo para silenciar o reactivar los avisos proactivos.
    mute_key: str = "<ctrl>+<alt>+m"
    # Mínimo de segundos entre dos avisos, sea cual sea la pantalla.
    min_interval_seconds: float = Field(default=5, ge=0, le=600)


class OutputConfig(_Model):
    answers: list[Output] = Field(default_factory=_answer_outputs)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)


class GuiConfig(_Model):
    port: int = Field(default=47821, ge=1024, le=65535)
    open_browser: bool = True


class AppConfig(_Model):
    version: int = 1
    profile: str = "generic"
    # Ventana elegida a mano cuando el perfil no identifica el juego.
    window_title: str | None = None
    # Carpeta de instalación elegida por el usuario, por id de perfil.
    game_dirs: dict[str, str] = Field(default_factory=dict)
    game_language: str = "en"
    output_language: str = "es"
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    gui: GuiConfig = Field(default_factory=GuiConfig)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} no contiene un objeto YAML")
    return AppConfig.model_validate(raw)


def save_config(config: AppConfig, path: Path) -> None:
    """Escritura atómica: nunca deja un fichero a medias."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".yaml", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
