"""Proveedores de modelos: locales por defecto, remotos opcionales (a partir del hito M6)."""

from __future__ import annotations

from gamepal.config import ProviderConfig
from gamepal.providers.base import (
    Capabilities,
    ChatResult,
    ImagePart,
    Message,
    Provider,
    ProviderError,
    Usage,
)
from gamepal.providers.openai_compat import LocalOpenAIProvider

__all__ = [
    "Capabilities",
    "ChatResult",
    "ImagePart",
    "LocalOpenAIProvider",
    "Message",
    "Provider",
    "ProviderError",
    "Usage",
    "create_provider",
]


def create_provider(config: ProviderConfig) -> Provider:
    if config.kind == "local-openai":
        return LocalOpenAIProvider(config.base_url, config.model, timeout=config.timeout_seconds)
    raise ProviderError(f"Proveedor no soportado: {config.kind}")
