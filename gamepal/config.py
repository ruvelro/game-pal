"""Configuración de la aplicación, validada con pydantic y guardada en YAML.

Nunca contiene secretos: las claves de API viven en el almacén de credenciales del sistema.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

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
    # local-openai: un servidor del jugador (Ollama, LM Studio…). managed: el motor que gestiona game-pal,
    # y entonces model, fast_model y vision_model son ids del catálogo de modelos.
    kind: Literal["local-openai", "managed"] = "local-openai"
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = ""
    # Modelo con visión: mira la pantalla al responder y resuelve pantallas dudosas. Vacío: sin visión.
    vision_model: str = ""
    # Si el modelo que razona también ve imágenes (Qwen 3.5/3.6, Gemma 3…), se usa para la visión sin pedirlo.
    auto_vision: bool = True
    # Modelo pequeño y rápido para decidir cómo responder y para respuestas breves. Vacío: no se usa.
    fast_model: str = ""
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)


class RuntimeConfig(_Model):
    """Motor llama.cpp gestionado."""

    # Variante del motor (id del catálogo) o auto: la mejor para este equipo.
    variant: str = "auto"
    context: int = Field(default=8192, ge=2048, le=262144)
    # Modelos cargados a la vez como máximo; el resto se carga al pedirlo.
    models_max: int = Field(default=2, ge=1, le=8)


Role = Literal["reasoning", "fast", "vision"]
ProviderName = Literal["local", "openai", "anthropic", "openrouter"]


class CloudProviderConfig(_Model):
    """Modelos de un proveedor en la nube para cada tarea. Vacío: el de razonamiento o el recomendado."""

    model: str = ""
    fast_model: str = ""
    vision_model: str = ""


class CloudConfig(_Model):
    openai: CloudProviderConfig = Field(default_factory=CloudProviderConfig)
    anthropic: CloudProviderConfig = Field(default_factory=CloudProviderConfig)
    openrouter: CloudProviderConfig = Field(default_factory=CloudProviderConfig)
    # Enviar capturas de pantalla a la nube es opcional y se activa a mano: pueden verse datos personales.
    allow_images: bool = False


class RoleRoute(_Model):
    provider: ProviderName = "local"
    fallback: Literal["none", "local", "openai", "anthropic", "openrouter"] = "none"


class RoutingConfig(_Model):
    """Qué proveedor hace cada tarea. Por defecto, todo en local."""

    reasoning: RoleRoute = Field(default_factory=RoleRoute)
    fast: RoleRoute = Field(default_factory=RoleRoute)
    vision: RoleRoute = Field(default_factory=RoleRoute)


class GenerationConfig(_Model):
    """Cuánto escribe y cuánto piensa el modelo al responder.

    Los modelos que razonan (Qwen 3, DeepSeek R1, gpt-oss, Claude…) piensan antes de contestar, y lo que
    piensan cuenta en el límite de la petición. game-pal suma el margen para pensar solo cuando el modelo
    razona, y las tareas internas (enrutar, buscar, leer pantallas) nunca piensan.
    """

    # Largo máximo de una respuesta, sin contar lo que el modelo piense antes.
    answer_tokens: int = Field(default=500, ge=150, le=4000)
    # Dejar pensar al modelo antes de responder. Desactivado: con los datos exactos que da game-pal (calendario,
    # tablas del perfil, fichas) no hace falta, y pensar multiplica el tiempo de respuesta (de ~7 s a ~45 s).
    thinking: bool = False
    # Margen para pensar, aparte de la respuesta.
    thinking_tokens: int = Field(default=2048, ge=512, le=16384)

    def think_budget(self) -> int:
        """Tokens para pensar en una respuesta al jugador: 0 si no se le deja."""
        return self.thinking_tokens if self.thinking else 0


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
    stop_phrases: list[str] = Field(
        default_factory=lambda: ["gracias", "para", "calla", "cállate", "basta", "silencio", "thanks", "stop"]
    )
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
    # Atajo que abre y cierra el chat sobre el juego, para escribirle sin salir. Vacío: sin atajo.
    chat_key: str = "<ctrl>+<alt>+c"


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


class KnowledgeConfig(_Model):
    # local: modelo multilingüe en ONNX (se descarga, 118 MB). provider: el servidor de modelos. none: solo palabras.
    embeddings: Literal["local", "provider", "none"] = "local"
    provider_model: str = ""
    max_pages: int = Field(default=150, ge=1, le=2000)
    rate_per_second: float = Field(default=1.0, ge=0.2, le=5)
    top_k: int = Field(default=4, ge=1, le=12)
    # Vuelve a comprobar las fuentes ya descargadas según la frecuencia del perfil.
    auto_refresh: bool = True
    # Antes de buscar, el modelo propone palabras clave (también traducidas al idioma de las fuentes).
    rewrite_queries: bool = True


class MemoryConfig(_Model):
    # Desactivada por defecto: no se guarda nada entre sesiones salvo que el jugador lo pida.
    enabled: bool = False
    # Resúmenes de sesiones anteriores que se dan al modelo como contexto.
    sessions_in_context: int = Field(default=3, ge=0, le=10)


class GuiConfig(_Model):
    port: int = Field(default=47821, ge=1024, le=65535)
    open_browser: bool = True


CONFIG_VERSION = 2


class AppConfig(_Model):
    version: int = CONFIG_VERSION
    # El asistente de primer uso se muestra hasta que el jugador lo termina o lo salta.
    setup_completed: bool = False
    # Nivel de hardware elegido en el asistente (0-3). Vacío: sin elegir.
    hardware_tier: int | None = Field(default=None, ge=0, le=3)
    profile: str = "generic"
    # Cambia solo al perfil del juego que esté abierto. Elegir un perfil a mano lo desactiva.
    auto_detect_game: bool = True
    # Ventana elegida a mano cuando el perfil no identifica el juego.
    window_title: str | None = None
    # Carpeta de instalación elegida por el usuario, por id de perfil.
    game_dirs: dict[str, str] = Field(default_factory=dict)
    # Carpeta de guardados elegida por el usuario, por id de perfil.
    save_dirs: dict[str, str] = Field(default_factory=dict)
    # Plugins de perfiles del usuario en los que se confía: id de perfil → sha256 del plugin.py.
    trusted_plugins: dict[str, str] = Field(default_factory=dict)
    game_language: str = "en"
    output_language: str = "es"
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    gui: GuiConfig = Field(default_factory=GuiConfig)


def first_run_config() -> AppConfig:
    """Sin configuración guardada: el juego y las respuestas, en el idioma del sistema."""
    from gamepal.system import system_language

    language = system_language()
    if language is None:
        return AppConfig()
    return AppConfig(game_language=language, output_language=language)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return first_run_config()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} no contiene un objeto YAML")
    return AppConfig.model_validate(migrate(raw))


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Pone al día una configuración guardada por una versión anterior."""
    # Las primeras versiones del asistente lo llamaban setup_done.
    if "setup_done" in raw:
        legacy = raw.pop("setup_done")
        raw.setdefault("setup_completed", legacy)
    version = raw.get("version", 1)
    if isinstance(version, int) and version < 2:
        # La versión 1 guardaba «pensar» activado por defecto; ahora va desactivado (respuestas en segundos).
        generation = raw.setdefault("generation", {})
        if isinstance(generation, dict):
            generation["thinking"] = False
        raw["version"] = 2
    return raw


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
