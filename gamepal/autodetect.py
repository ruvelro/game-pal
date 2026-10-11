"""Detección automática del servidor y el modelo local, y del juego abierto.

Solo rellena huecos: nunca cambia un modelo, una ventana o un perfil que el jugador eligió a mano.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gamepal.capture import WindowInfo, match_window
from gamepal.checks import Probe, discover_servers, probe_local, server_name
from gamepal.config import AppConfig
from gamepal.profiles import LoadedProfile
from gamepal.providers import ProviderError

# Modelos que no sirven para conversar: embeddings, reordenadores, voz…
_NOT_CHAT = re.compile(r"embed|bge|(^|[/:_-])e5([/:_-]|$)|minilm|rerank|whisper|tts|clip", re.IGNORECASE)
_SIZE = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z])", re.IGNORECASE)
# Por encima de este tamaño los consejos tardan demasiado para jugar; el jugador puede elegir uno mayor.
PREFERRED_MAX_B = 14.0
# Memoria aproximada por cada mil millones de parámetros con la cuantización habitual (Q4) y su contexto.
GB_PER_B = 0.7


@dataclass(frozen=True, slots=True)
class ModelChoice:
    server: str
    url: str
    model: str


def model_size_b(name: str) -> float | None:
    """Miles de millones de parámetros según el nombre («qwen3:4b», «Llama-3.1-8B-Instruct»)."""
    match = _SIZE.search(name.replace(":", " ").replace("/", " "))
    return float(match.group(1)) if match else None


def pick_model(models: tuple[str, ...] | list[str], memory_gb: float | None) -> str | None:
    """El modelo de conversación más capaz que cabe en la memoria del equipo, sin pasar de PREFERRED_MAX_B."""
    chat = [m for m in models if not _NOT_CHAT.search(m)]
    if not chat:
        return None
    # Se deja la mitad de la memoria para el juego y el sistema.
    budget_b = ((memory_gb or 8.0) * 0.5) / GB_PER_B
    limit = min(budget_b, PREFERRED_MAX_B)
    sized = [(size, m) for m in chat if (size := model_size_b(m)) is not None]
    fitting = [(size, m) for size, m in sized if size <= limit]
    if fitting:
        return max(fitting)[1]
    unknown = sorted(m for m in chat if model_size_b(m) is None)
    if unknown:
        return unknown[0]
    return min(sized)[1]


async def choose_local_model(
    config: AppConfig, memory_gb: float | None, probe: Probe | None = None
) -> ModelChoice | None:
    """Servidor y modelo locales cuando aún no hay modelo elegido."""
    if config.provider.model:
        return None
    probe = probe or probe_local
    url = config.provider.base_url
    try:
        servers = [(server_name(url), url, await probe(url))]
    except (ProviderError, ValueError):
        servers = await discover_servers(probe, skip=url)
    for name, server_url, models in servers:
        model = pick_model(models, memory_gb)
        if model:
            return ModelChoice(name, server_url, model)
    return None


def _target(loaded: LoadedProfile) -> tuple[list[str], str | None]:
    game = loaded.profile.game
    return list(game.process), game.window_title


def game_is_open(loaded: LoadedProfile, windows: list[WindowInfo], foreground: int | None) -> bool:
    processes, title = _target(loaded)
    return bool(processes or title) and match_window(windows, processes, title, foreground) is not None


def detect_game(profiles: dict[str, LoadedProfile], windows: list[WindowInfo], foreground: int | None) -> str | None:
    """El perfil cuyo juego está abierto. Si hay varios, el de primer plano y luego el de ventana más grande."""
    found: list[tuple[bool, int, str]] = []
    for profile_id, loaded in profiles.items():
        processes, title = _target(loaded)
        if not processes and not title:
            continue
        window = match_window(windows, processes, title, foreground)
        if window is not None:
            found.append((window.pid == foreground, window.area, profile_id))
    return max(found)[2] if found else None


def next_profile(
    config: AppConfig,
    current: LoadedProfile | None,
    profiles: dict[str, LoadedProfile],
    windows: list[WindowInfo],
    foreground: int | None,
) -> str | None:
    """A qué perfil cambiar, o None si hay que dejar el actual."""
    if not config.auto_detect_game or config.window_title:
        return None
    if current is not None and game_is_open(current, windows, foreground):
        return None
    detected = detect_game(profiles, windows, foreground)
    return detected if detected and detected != config.profile else None
