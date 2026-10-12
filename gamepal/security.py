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


# Nombres que solo existen dentro de una red privada: mDNS (.local), routers domésticos y Tailscale.
PRIVATE_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain", ".ts.net")
# Tailscale y otras VPN usan el espacio compartido 100.64.0.0/10, que ipaddress no da por privado.
_SHARED = ipaddress.ip_network("100.64.0.0/10")


def is_private_host(host: str | None) -> bool:
    """Este equipo u otro de la red de casa: IP privada o un nombre que solo resuelve dentro de la red."""
    if not host:
        return False
    host = host.strip("[]").lower().rstrip(".")
    if is_loopback_host(host):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Un nombre sin puntos («mi-pc») solo se resuelve dentro de la red.
        return "." not in host or host.endswith(PRIVATE_SUFFIXES)
    if address.is_multicast or address.is_unspecified:
        return False
    return address.is_private or address.is_link_local or (address.version == 4 and address in _SHARED)


def validate_model_server(url: str) -> str:
    """El servidor de modelos del jugador: en este equipo, en su red o, por https, en cualquier sitio.

    Dentro de la red de casa vale http (Ollama o LM Studio en otro ordenador no suelen tener certificado);
    fuera de ella, el texto de la pantalla y las preguntas solo viajan cifrados.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrlError("La dirección del servidor debe empezar por http:// o https://")
    if parts.username or parts.password:
        raise UnsafeUrlError("No incluyas usuario ni contraseña en la dirección; usa el campo de la clave")
    if parts.query or parts.fragment:
        raise UnsafeUrlError("La dirección no debe llevar parámetros ni fragmento")
    if not parts.hostname:
        raise UnsafeUrlError("Falta el nombre o la IP del servidor")
    if parts.scheme == "http" and not is_private_host(parts.hostname):
        raise UnsafeUrlError(
            "Un servidor fuera de tu red solo se admite por https://. Dentro de tu red vale http:// "
            "con su IP (192.168.x.x, 10.x.x.x) o su nombre (.local)."
        )
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
