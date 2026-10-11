"""Configuración que propone el asistente de primer uso para cada nivel de hardware."""

from __future__ import annotations

from typing import Literal

from gamepal.config import AppConfig, RoleRoute, RoutingConfig
from gamepal.runtime.catalog import Catalog

Engine = Literal["managed", "external", "cloud"]


def apply_tier(config: AppConfig, catalog: Catalog, tier: int, engine: Engine) -> AppConfig:
    """Devuelve una configuración nueva con los modelos y la voz del nivel elegido.

    - managed: el motor de game-pal con los modelos del catálogo, y todas las tareas en local.
    - external: el servidor propio del jugador; solo se ajusta la voz (sus modelos los elige él).
    - cloud: no se tocan los proveedores; solo se guarda el nivel y la voz.
    """
    preset = catalog.tier(tier)
    data = config.model_dump()
    data["hardware_tier"] = tier
    data["voice"]["stt"]["model"] = preset.stt
    if engine == "managed":
        data["provider"].update(
            kind="managed", model=preset.reasoning, fast_model=preset.fast, vision_model=preset.vision
        )
        data["runtime"].update(context=preset.context, models_max=preset.models_max)
        local = RoleRoute(provider="local", fallback="none").model_dump()
        data["routing"] = RoutingConfig.model_validate(
            {"reasoning": local, "fast": local, "vision": local}
        ).model_dump()
    elif engine == "external" and config.provider.kind == "managed":
        data["provider"].update(kind="local-openai", model="", fast_model="", vision_model="")
    return AppConfig.model_validate(data)
