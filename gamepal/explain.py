"""Errores de los modelos explicados en lenguaje llano, con qué hacer para arreglarlos.

Los proveedores dan mensajes técnicos («local respondió 404: model not found»); el jugador ve qué pasa
y qué puede hacer. El mensaje original queda en el registro.
"""

from __future__ import annotations

import re

from gamepal.checks import CLOUD_NAMES, server_name
from gamepal.config import AppConfig
from gamepal.providers.base import EMPTY_ANSWER, OUT_OF_ROOM

__all__ = ["EMPTY_ANSWER", "OUT_OF_ROOM", "explain_error"]

_PROVIDER = re.compile(r"\b(local|openai|openrouter|anthropic|claude)\b", re.IGNORECASE)
_STATUS = re.compile(r"respondió (\d{3})")
_MODELS_PAGE = "en la página Modelos"
# Cómo dice cada servidor que no tiene el modelo: Ollama («model "x" not found», y desde la 0.40 un 502
# «no available node advertises the requested model»), LM Studio, llama.cpp y vLLM.
_MISSING_MODEL = re.compile(
    r"model\b.{0,80}?not found|no available node advertises the requested model|model .* does not exist"
    r"|unknown model|model_not_found|no such model|failed to load model",
    re.IGNORECASE,
)


def explain_error(message: str, config: AppConfig) -> str:
    """Un error de un proveedor de modelos, explicado. Si no se reconoce, se devuelve tal cual."""
    found = _PROVIDER.search(message)
    who = found.group(1).lower() if found else ""
    if who == "claude":
        who = "anthropic"
    status = int(match.group(1)) if (match := _STATUS.search(message)) else None
    lowered = message.lower()

    if OUT_OF_ROOM in lowered:
        return (
            "El modelo se ha quedado pensando y no ha llegado a responder. Dale más margen para pensar, "
            "o desactiva que piense antes de responder, en las opciones avanzadas de la página Modelos."
        )
    if EMPTY_ANSWER in lowered:
        return f"El modelo no ha devuelto ninguna respuesta. Vuelve a preguntar o prueba otro modelo {_MODELS_PAGE}."

    if who == "local":
        url = config.provider.base_url
        server = server_name(url)
        model = config.provider.model
        if "no hay modelo seleccionado" in lowered:
            return (
                f"Aún no has elegido el modelo de IA. Elígelo {_MODELS_PAGE}; si {server} ya está abierto, "
                "game-pal lo elige solo en unos segundos."
            )
        if "no se pudo contactar" in lowered:
            return (
                f"No consigo hablar con {server} en {url}. ¿Está abierto? "
                f"Si usas otro programa de modelos, elígelo {_MODELS_PAGE}."
            )
        if model and (status == 404 or _MISSING_MODEL.search(message)):
            pull = f" (en Ollama: ollama pull {model})" if server == "Ollama" else ""
            return f"{server} no tiene el modelo «{model}». Descárgalo{pull} o elige otro {_MODELS_PAGE}."
        if status is not None and status >= 500:
            return (
                f"{server} ha fallado al responder (error {status}). Puede que el modelo no quepa en memoria "
                f"(prueba uno más pequeño {_MODELS_PAGE}) o que otro programa esté usando el puerto de {url}."
            )

    cloud = CLOUD_NAMES.get(who, "")
    if cloud:
        if "no hay modelo seleccionado" in lowered:
            return f"Falta elegir el modelo de {cloud} {_MODELS_PAGE}."
        if "rechazó la clave" in lowered or status in (401, 403):
            return (
                f"{cloud} no acepta la clave de API: puede que no sea válida o haya caducado. Cámbiala {_MODELS_PAGE}."
            )
        if "límite" in lowered or status == 429:
            return f"{cloud} dice que has superado tu límite de uso. Espera un poco o revisa tu cuenta."
        if "no se pudo contactar" in lowered:
            return f"No consigo conectar con {cloud}. Comprueba tu conexión a internet."

    if "se cortó la conexión" in lowered or "timed out" in lowered or "timeout" in lowered:
        return (
            "El modelo ha tardado demasiado o se ha cortado la conexión. "
            f"Si pasa a menudo, prueba un modelo más pequeño {_MODELS_PAGE}."
        )
    return message
