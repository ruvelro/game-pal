"""Configuración de la aplicación, validada con pydantic y guardada en YAML.

Nunca contiene secretos: las claves de API viven en el almacén de credenciales del sistema.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

OcrBackendName = Literal["rapidocr", "apple-vision", "none"]


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
    backend: OcrBackendName = "rapidocr"
    min_confidence: float = Field(default=0.5, ge=0, le=1)


class ProviderConfig(_Model):
    kind: Literal["local-openai"] = "local-openai"
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = ""
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)


class GuiConfig(_Model):
    port: int = Field(default=47821, ge=1024, le=65535)
    open_browser: bool = True


class AppConfig(_Model):
    version: int = 1
    profile: str = "generic"
    # Ventana elegida a mano cuando el perfil no identifica el juego.
    window_title: str | None = None
    game_language: str = "en"
    output_language: str = "es"
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
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
