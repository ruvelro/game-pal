"""Reglas de seguridad compartidas."""

from __future__ import annotations

import hmac
import ipaddress
import secrets
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class UnsafeUrlError(ValueError):
    pass


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_local_endpoint(url: str) -> str:
    """Un endpoint local debe ser http(s) en loopback, sin credenciales ni parámetros."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrlError("El endpoint local debe empezar por http:// o https://")
    if parts.username or parts.password:
        raise UnsafeUrlError("No incluyas credenciales en la URL")
    if parts.query or parts.fragment:
        raise UnsafeUrlError("La URL no debe llevar parámetros ni fragmento")
    if not is_loopback_host(parts.hostname):
        raise UnsafeUrlError("Un proveedor local solo puede apuntar a este equipo (127.0.0.1, ::1 o localhost)")
    return url.strip().rstrip("/")


def validate_remote_endpoint(url: str) -> str:
    """Un endpoint remoto debe ir siempre por HTTPS."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise UnsafeUrlError("Los proveedores remotos solo se admiten por https://")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UnsafeUrlError("La URL no debe llevar credenciales, parámetros ni fragmento")
    if not parts.hostname:
        raise UnsafeUrlError("Falta el nombre del servidor")
    return url.strip().rstrip("/")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(expected: str, given: str | None) -> bool:
    if not given:
        return False
    return hmac.compare_digest(expected.encode(), given.encode())


def allowed_host_header(host_header: str | None, port: int) -> bool:
    """Protección frente a DNS rebinding: solo se aceptan nombres de loopback con nuestro puerto."""
    if not host_header:
        return False
    host_header = host_header.strip().lower()
    return host_header in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
