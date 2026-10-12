"""Claves de API en el almacén de credenciales del sistema (Llavero, Administrador de credenciales, Secret Service).

Nunca se escriben en la configuración ni en el registro, y la API local solo informa de si existen.
"""

from __future__ import annotations

import logging
from typing import Literal

log = logging.getLogger(__name__)

SERVICE = "game-pal"
CloudName = Literal["openai", "anthropic", "openrouter"]
CLOUDS: tuple[CloudName, ...] = ("openai", "anthropic", "openrouter")
# Además de las nubes, la clave del servidor de modelos del jugador, si la pide (vLLM, LM Studio con clave…).
SecretName = Literal["openai", "anthropic", "openrouter", "server"]
KEYS: tuple[SecretName, ...] = (*CLOUDS, "server")


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    """Envoltorio fino sobre keyring. Las pruebas usan MemorySecretStore."""

    def get(self, name: SecretName) -> str | None:
        try:
            import keyring

            return keyring.get_password(SERVICE, name)
        except Exception as exc:
            log.warning("No se pudo leer la clave de %s: %s", name, type(exc).__name__)
            return None

    def set(self, name: SecretName, value: str) -> None:
        try:
            import keyring

            keyring.set_password(SERVICE, name, value)
        except Exception as exc:
            raise SecretStoreError(
                "No hay almacén de credenciales disponible en este sistema. En Linux instala y abre "
                "un llavero compatible con Secret Service (GNOME Keyring o KWallet)."
            ) from exc

    def delete(self, name: SecretName) -> None:
        try:
            import keyring

            keyring.delete_password(SERVICE, name)
        except Exception as exc:
            log.debug("No había clave de %s que borrar: %s", name, type(exc).__name__)

    def configured(self) -> dict[str, bool]:
        return {name: bool(self.get(name)) for name in KEYS}


class MemorySecretStore(SecretStore):
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: SecretName) -> str | None:
        return self._values.get(name)

    def set(self, name: SecretName, value: str) -> None:
        self._values[name] = value

    def delete(self, name: SecretName) -> None:
        self._values.pop(name, None)
