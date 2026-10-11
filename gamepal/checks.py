"""Comprobaciones de puesta en marcha: qué funciona, qué falta y cómo arreglarlo.

Las usan `game-pal doctor` (sin daemon) y la GUI (GET /api/setup, con el daemon en marcha). Cada
comprobación dice en lenguaje llano qué pasa y, si algo falla, qué hacer, con el comando cuando lo hay.
"""

from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import os
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError

from gamepal import ui_build
from gamepal.capture import CaptureBackend, CaptureUnavailableError, WindowInfo, create_backend, match_window
from gamepal.config import AppConfig, OcrBackendName, load_config, rapidocr_available
from gamepal.paths import AppPaths, bundled_profiles_dir
from gamepal.profiles import LoadedProfile, ProfileError, ProfileStore
from gamepal.providers import LocalOpenAIProvider, ProviderError
from gamepal.system import can_open_settings
from gamepal.voice.audio import list_devices

if TYPE_CHECKING:
    from gamepal.daemon import Daemon
    from gamepal.runtime.llama import LlamaRuntime

CheckState = Literal["ok", "warn", "error", "off"]

KNOWN_SERVERS: tuple[tuple[str, str], ...] = (
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("llama.cpp", "http://127.0.0.1:8080/v1"),
    ("MLX", "http://127.0.0.1:8081/v1"),
)
CLOUD_NAMES = {"openai": "OpenAI", "anthropic": "Anthropic (Claude)", "openrouter": "OpenRouter"}
STARTER_MODEL = "qwen3:4b"
AUTO_PICK = (
    "game-pal elegirá uno solo al arrancar (o en unos segundos, si ya está en marcha). "
    "También puedes elegirlo tú en la página Modelos."
)
PROBE_TIMEOUT_S = 3.0

Probe = Callable[[str], Awaitable[tuple[str, ...]]]


class Check(BaseModel):
    id: str
    title: str
    state: CheckState
    detail: str
    # Qué hacer para arreglarlo, en lenguaje llano.
    fix: str = ""
    # Un comando para copiar y pegar, si ayuda.
    command: str = ""
    # Panel de ajustes del sistema que game-pal puede abrir para arreglarlo (ver system.SETTINGS_PANES).
    settings: str = ""
    # Sin esto game-pal no puede aconsejar; lo demás es opcional.
    required: bool = True


def blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.state == "error" and c.required]


# Permisos del sistema ----------------------------------------------------------------------


def screen_capture_allowed() -> bool | None:
    """En macOS, si la app tiene permiso de Grabación de pantalla. None si no aplica o no se sabe."""
    if sys.platform != "darwin":
        return None
    try:
        import Quartz

        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def accessibility_trusted() -> bool | None:
    """En macOS, si la app puede escuchar el teclado para los atajos globales."""
    if sys.platform != "darwin":
        return None
    try:
        lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except (OSError, AttributeError):
        return None


def _wayland() -> bool:
    return sys.platform.startswith("linux") and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def _mac_privacy(pane: str) -> str:
    return (
        f"Abre Ajustes del Sistema › Privacidad y seguridad › {pane}, activa la app desde la que lanzas "
        "game-pal (Terminal, iTerm, VS Code…) y vuelve a arrancar game-pal."
    )


# Comprobaciones sueltas ----------------------------------------------------------------------


def check_gui() -> Check:
    title = "Interfaz"
    built = (ui_build.STATIC_DIR / "index.html").is_file()
    source = ui_build.source_dir()
    if source is None:
        if built:
            return Check(id="gui", title=title, state="ok", detail="Incluida")
        return Check(id="gui", title=title, state="error", detail="Esta instalación no incluye la interfaz")
    stale = ui_build.needs_build(source)
    if not stale:
        return Check(id="gui", title=title, state="ok", detail="Compilada")
    try:
        ui_build.find_node()
    except ui_build.UiBuildError as exc:
        if built:
            return Check(id="gui", title=title, state="warn", required=False, detail="Desactualizada", fix=str(exc))
        return Check(id="gui", title=title, state="error", detail="Sin compilar", fix=str(exc))
    detail = "Se actualizará al arrancar" if built else "Se compilará sola al arrancar (la primera vez tarda un poco)"
    return Check(id="gui", title=title, state="ok", detail=detail)


def check_config(paths: AppPaths) -> tuple[Check, AppConfig]:
    path = paths.config_file
    try:
        config = load_config(path)
    except (ValueError, ValidationError, OSError) as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        check = Check(
            id="config",
            title="Configuración",
            state="error",
            detail=f"{path} no es válida: {first}",
            fix="Corrígela o bórrala: game-pal creará una nueva con los valores por defecto.",
        )
        return check, AppConfig()
    detail = str(path) if path.exists() else "Valores por defecto (aún no has cambiado nada)"
    return Check(id="config", title="Configuración", state="ok", detail=detail), config


def check_capture(backend: CaptureBackend | None, error: str | None, windows: list[WindowInfo] | None) -> Check:
    title = "Captura de pantalla"
    if screen_capture_allowed() is False:
        return Check(
            id="capture",
            title=title,
            state="error",
            detail="macOS no da permiso de Grabación de pantalla",
            fix=_mac_privacy("Grabación de pantalla"),
            settings="screen-recording",
        )
    if backend is None or windows is None:
        fix = "Vuelve a arrancar game-pal."
        if _wayland():
            fix = (
                "En Wayland, game-pal solo ve las ventanas de XWayland. "
                "Usa una sesión X11 o lanza el juego con XWayland."
            )
        elif sys.platform.startswith("linux"):
            fix = "game-pal necesita un escritorio X11 (o XWayland) con DISPLAY definido."
        return Check(id="capture", title=title, state="error", detail=error or "No disponible", fix=fix)
    visible = [w for w in windows if w.width >= 200 and w.height >= 150]
    return Check(id="capture", title=title, state="ok", detail=f"{backend.name}: ve {len(visible)} ventanas")


def check_game(
    config: AppConfig,
    profile: LoadedProfile | None,
    profile_error: str | None,
    windows: list[WindowInfo] | None,
    foreground: int | None,
) -> Check:
    title = "Juego"
    if profile is None:
        return Check(
            id="game",
            title=title,
            state="error",
            detail=f"No se pudo cargar el perfil «{config.profile}»: {profile_error or 'no existe'}",
            fix="Elige otro perfil en la página Juego o corrige el suyo (game-pal profiles te dice qué falla).",
        )
    name = profile.profile.name
    game = profile.profile.game
    processes, wanted = list(game.process), game.window_title
    if config.window_title:
        processes, wanted = [], config.window_title
    if not processes and not wanted:
        return Check(
            id="game",
            title=title,
            state="warn",
            required=False,
            detail=f"Perfil {name}: aún no sabe qué ventana mirar",
            fix="Abre el juego y elige su ventana en la página Juego.",
        )
    if windows is None:
        return Check(id="game", title=title, state="off", required=False, detail=f"Perfil {name}")
    window = match_window(windows, processes, wanted, foreground)
    if window is None:
        return Check(
            id="game",
            title=title,
            state="warn",
            required=False,
            detail=f"Perfil {name}: el juego no está abierto",
            fix="Ábrelo en modo ventana o ventana sin bordes: la pantalla completa exclusiva no se puede capturar.",
        )
    label = window.title or window.process
    return Check(id="game", title=title, state="ok", detail=f"{name}: «{label}» ({window.width}×{window.height})")


def ocr_problem(backend: OcrBackendName) -> str | None:
    """Sin cargar el motor: si su biblioteca está instalada en este sistema."""
    if backend == "rapidocr" and not rapidocr_available():
        return "RapidOCR no está instalado en este sistema"
    if backend == "apple-vision" and (sys.platform != "darwin" or importlib.util.find_spec("Vision") is None):
        return "Apple Vision no está disponible"
    if backend == "windows-ocr" and sys.platform != "win32":
        return "Windows OCR solo existe en Windows"
    return None


def check_ocr(config: AppConfig, ready: str | None, error: str | None) -> Check:
    title = "Lectura de texto"
    backend = config.ocr.backend
    if backend == "none":
        return Check(
            id="ocr",
            title=title,
            state="warn",
            required=False,
            detail="Desactivada: game-pal no leerá el texto del juego",
            fix="Actívala en Ajustes para que entienda lo que pasa en pantalla.",
        )
    if error:
        fix = "Elige otro motor de lectura en Ajustes."
        command = ""
        if backend == "apple-vision" and sys.platform == "darwin":
            fix, command = (
                "Instala el extra de Apple Vision o elige otro motor en Ajustes.",
                "uv sync --extra macos-vision",
            )
        elif backend == "rapidocr" and sys.platform == "win32":
            fix = "En Windows ARM usa Windows OCR (Ajustes › Lectura)."
        return Check(id="ocr", title=title, state="error", detail=error, fix=fix, command=command)
    return Check(id="ocr", title=title, state="ok", detail=ready or backend)


def server_name(url: str) -> str:
    port = urlsplit(url).port
    for name, known in KNOWN_SERVERS:
        if urlsplit(known).port == port:
            return name
    return "el servidor local"


async def probe_local(url: str) -> tuple[str, ...]:
    """Los modelos que ofrece un servidor local compatible con OpenAI."""
    provider = LocalOpenAIProvider(url, timeout=PROBE_TIMEOUT_S)
    try:
        return (await provider.capabilities()).models
    finally:
        await provider.aclose()


async def discover_servers(
    probe: Probe | None = None, *, skip: str | None = None, include: str | None = None
) -> list[tuple[str, str, tuple[str, ...]]]:
    """Servidores de modelos que responden en este equipo: (nombre, url, modelos).

    Prueba los conocidos y, con `include`, también una dirección propia del jugador.
    """
    probe = probe or probe_local
    skip_port = urlsplit(skip).port if skip else None
    candidates = [(name, url) for name, url in KNOWN_SERVERS if urlsplit(url).port != skip_port]
    if include and all(urlsplit(url).port != urlsplit(include).port for _, url in KNOWN_SERVERS):
        candidates.insert(0, (server_name(include), include))

    async def one(url: str) -> tuple[str, ...] | None:
        try:
            return await probe(url)
        except (ProviderError, ValueError):
            return None

    found = await asyncio.gather(*(one(url) for _, url in candidates))
    return [(name, url, models) for (name, url), models in zip(candidates, found, strict=True) if models is not None]


async def check_models(config: AppConfig, has_key: Callable[[str], bool], probe: Probe | None = None) -> Check:
    """El modelo que razona las respuestas: sin él, game-pal no puede aconsejar."""
    from gamepal.providers.hub import model_for

    probe = probe or probe_local
    title = "Modelo de IA"
    route = config.routing.reasoning
    if route.provider != "local":
        cloud = CLOUD_NAMES[route.provider]
        if not await asyncio.to_thread(has_key, route.provider):
            return Check(
                id="model",
                title=title,
                state="error",
                detail=f"Las respuestas van a {cloud}, pero no hay clave guardada",
                fix=f"Añade la clave de {cloud} en la página Modelos, o vuelve al modo local.",
            )
        model = model_for(config, route.provider, "reasoning")
        if not model:
            return Check(
                id="model",
                title=title,
                state="error",
                detail=f"Falta elegir el modelo de {cloud}",
                fix="Elígelo en la página Modelos.",
            )
        return Check(id="model", title=title, state="ok", detail=f"{model} en {cloud}")

    url = config.provider.base_url
    name = server_name(url)
    try:
        models = await probe(url)
    except (ProviderError, ValueError) as exc:
        others = await discover_servers(probe, skip=url)
        if others:
            names = ", ".join(f"{other} ({other_url})" for other, other_url, _ in others)
            return Check(
                id="model",
                title=title,
                state="error",
                detail=f"{name} no responde en {url}",
                fix=f"Hay otro servidor en marcha: {names}. "
                + (AUTO_PICK if not config.provider.model else "Elígelo en la página Modelos."),
            )
        if "respondió" in str(exc):
            # Algo escucha en ese puerto pero no es un servidor de modelos que funcione.
            return Check(
                id="model",
                title=title,
                state="error",
                detail=f"En {url} responde otro programa, no {name}",
                fix=(
                    f"Algo ocupa el puerto de {name} y contesta con error. Si tienes {name}, cierra ese programa "
                    f"y vuelve a abrir {name}; si no, elige otro servidor en la página Modelos."
                ),
            )
        if name == "Ollama":
            return Check(
                id="model",
                title=title,
                state="error",
                detail="No hay ningún servidor de modelos en marcha",
                fix=(
                    "Instala Ollama (https://ollama.com), ábrelo y descarga un modelo con el comando de abajo. "
                    "También valen LM Studio, llama.cpp o MLX, o una clave de OpenAI, Claude u OpenRouter "
                    "en la página Modelos."
                ),
                command=f"ollama pull {STARTER_MODEL}",
            )
        return Check(
            id="model",
            title=title,
            state="error",
            detail=f"{name} no responde en {url}",
            fix="Arráncalo, o elige otro servidor en la página Modelos.",
        )

    pull = name == "Ollama"
    if not models:
        return Check(
            id="model",
            title=title,
            state="error",
            detail=f"{name} funciona, pero no tiene ningún modelo",
            fix="Descarga uno con el comando de abajo." if pull else f"Descarga un modelo desde {name}.",
            command=f"ollama pull {STARTER_MODEL}" if pull else "",
        )
    chosen = config.provider.model
    if not chosen:
        listed = ", ".join(models[:5]) + ("…" if len(models) > 5 else "")
        return Check(
            id="model",
            title=title,
            state="error",
            detail=f"{name} tiene {len(models)} modelos, pero no has elegido ninguno",
            fix=f"{AUTO_PICK} Modelos disponibles: {listed}.",
        )
    if chosen not in models:
        return Check(
            id="model",
            title=title,
            state="error",
            detail=f"{name} no tiene el modelo «{chosen}»",
            fix="Descárgalo con el comando de abajo o elige otro en la página Modelos."
            if pull
            else "Elige otro en la página Modelos.",
            command=f"ollama pull {chosen}" if pull else "",
        )
    return Check(id="model", title=title, state="ok", detail=f"{chosen} en {name}")


def check_engine(config: AppConfig, runtime: LlamaRuntime, error: str | None = None) -> Check:
    """El motor llama.cpp que gestiona game-pal, con los modelos de su catálogo."""
    title = "Modelo de IA"
    fix = "Instálalo en la página Modelos o con el asistente (Avanzado › Abrir el asistente de configuración)."
    installed = {m.id: m for m in runtime.installed_models()}
    if not installed:
        return Check(
            id="model", title=title, state="error", detail="El motor de game-pal no tiene ningún modelo", fix=fix
        )
    chosen = installed.get(config.provider.model)
    if chosen is None:
        entry = runtime.catalog.model(config.provider.model)
        name = entry.name if entry else config.provider.model or "—"
        return Check(id="model", title=title, state="error", detail=f"El modelo «{name}» no está instalado", fix=fix)
    if error:
        return Check(
            id="model",
            title=title,
            state="error",
            detail=f"El motor de game-pal no arrancó: {error[:300]}",
            fix="Vuelve a arrancar game-pal. Si sigue fallando, prueba un modelo más pequeño en la página Modelos.",
        )
    return Check(id="model", title=title, state="ok", detail=f"{chosen.name} en el motor de game-pal")


def _uses_engine(config: AppConfig) -> bool:
    return config.routing.reasoning.provider == "local" and config.provider.kind == "managed"


def check_voice(config: AppConfig, devices: Callable[[], dict[str, list[str]]] | None = None) -> Check:
    title = "Voz"
    voice = config.voice
    if not voice.enabled and "voice" not in config.output.answers:
        return Check(
            id="voice",
            title=title,
            state="off",
            required=False,
            detail="Desactivada",
            fix="Actívala en la página Voz si quieres hablarle o que te conteste en voz alta.",
        )
    try:
        found = (devices or list_devices)()
    except Exception as exc:
        fix = (
            "Instala PortAudio (sudo apt install libportaudio2) y vuelve a arrancar."
            if sys.platform.startswith("linux")
            else "Comprueba que el sistema tiene audio y vuelve a arrancar."
        )
        return Check(id="voice", title=title, state="error", required=False, detail=f"Sin audio: {exc}", fix=fix)
    if voice.enabled and not found.get("inputs"):
        return Check(
            id="voice",
            title=title,
            state="error",
            required=False,
            detail="No encuentro ningún micrófono",
            fix="Conecta uno o elige otro en la página Voz.",
        )
    detail = f"Micrófono: {voice.input_device or 'el predeterminado'}" if voice.enabled else "Solo respuestas habladas"
    fix = _mac_privacy("Micrófono") if voice.enabled and sys.platform == "darwin" else ""
    if fix:
        fix = "Si no te oye: " + fix[0].lower() + fix[1:]
    pane = "microphone" if voice.enabled and can_open_settings("microphone") else ""
    return Check(id="voice", title=title, state="ok", required=False, detail=detail, fix=fix, settings=pane)


def check_hotkeys(config: AppConfig) -> Check:
    title = "Atajos de teclado"
    ptt = config.voice.push_to_talk
    proactive = config.output.proactive
    keys = []
    if config.voice.enabled and ptt.enabled and ptt.key:
        keys.append(f"{ptt.key} para hablar")
    if proactive.enabled and proactive.mute_key:
        keys.append(f"{proactive.mute_key} para silenciar avisos")
    if not keys:
        return Check(id="hotkeys", title=title, state="off", required=False, detail="Ninguno activo")
    if accessibility_trusted() is False:
        return Check(
            id="hotkeys",
            title=title,
            state="warn",
            required=False,
            detail="macOS no deja escuchar el teclado",
            fix=_mac_privacy("Accesibilidad (y Monitorización de entrada)"),
            settings="accessibility",
        )
    if _wayland():
        return Check(
            id="hotkeys",
            title=title,
            state="warn",
            required=False,
            detail="En Wayland los atajos globales no funcionan",
            fix="Usa una sesión X11, o los botones de la GUI y la voz.",
        )
    return Check(id="hotkeys", title=title, state="ok", required=False, detail=" · ".join(keys))


# Conjuntos -----------------------------------------------------------------------------------


async def _windows(backend: CaptureBackend | None) -> tuple[list[WindowInfo] | None, int | None, str | None]:
    if backend is None:
        return None, None, None
    try:
        windows = await asyncio.to_thread(backend.list_windows)
        foreground = await asyncio.to_thread(backend.foreground_pid)
    except (CaptureUnavailableError, OSError) as exc:
        return None, None, str(exc)
    return windows, foreground, None


async def doctor_checks(paths: AppPaths, *, probe: Probe | None = None) -> list[Check]:
    """Sin daemon: lo que comprueba `game-pal doctor`."""
    from gamepal.keystore import SecretStore

    config_check, config = check_config(paths)
    checks = [check_gui(), config_check]

    profile: LoadedProfile | None = None
    profile_error: str | None = None
    try:
        profile = ProfileStore(bundled_profiles_dir(), paths.user_profiles_dir).get(config.profile)
    except ProfileError as exc:
        profile_error = str(exc)

    backend: CaptureBackend | None = None
    capture_error: str | None = None
    if screen_capture_allowed() is not False:  # sin permiso, crear el backend volvería a pedirlo
        try:
            backend = create_backend()
        except CaptureUnavailableError as exc:
            capture_error = str(exc)
    windows, foreground, list_error = await _windows(backend)
    checks.append(check_capture(backend, capture_error or list_error, windows))
    checks.append(check_game(config, profile, profile_error, windows, foreground))

    problem = ocr_problem(config.ocr.backend)
    checks.append(check_ocr(config, None if problem else config.ocr.backend, problem))
    if _uses_engine(config):
        from gamepal.runtime.catalog import bundled_catalog
        from gamepal.runtime.llama import LlamaRuntime

        checks.append(check_engine(config, LlamaRuntime(paths.data_dir / "engine", paths.log_dir, bundled_catalog())))
    else:
        secrets = SecretStore()
        has_key = lambda name: bool(secrets.get(name))  # type: ignore[arg-type]  # noqa: E731
        checks.append(await check_models(config, has_key, probe))
    checks.append(check_voice(config))
    checks.append(check_hotkeys(config))
    return checks


async def daemon_checks(daemon: Daemon, *, probe: Probe | None = None) -> list[Check]:
    """Con el daemon en marcha: el estado real de cada componente."""
    c, config = daemon.c, daemon.config
    probe = probe or daemon.probe
    windows, foreground, list_error = await _windows(c.backend)
    if _uses_engine(config):
        runtime = daemon.engine.runtime
        model = check_engine(config, runtime, (runtime.detail or "error") if runtime.state == "error" else None)
    else:
        model = await check_models(config, lambda name: bool(daemon.secrets.get(name)), probe)  # type: ignore[arg-type]
    return [
        check_capture(c.backend, c.errors.get("capture") or list_error, windows),
        check_game(config, c.profile, c.errors.get("profile"), windows, foreground),
        check_ocr(config, c.ocr.name if c.ocr else None, c.errors.get("ocr")),
        model,
        check_voice(config),
        check_hotkeys(config),
    ]
