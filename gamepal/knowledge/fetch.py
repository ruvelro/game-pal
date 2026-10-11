"""Descargas web seguras y educadas para las fuentes de conocimiento.

- Solo http(s) hacia direcciones públicas: nada de localhost ni de la red local, también tras redirecciones.
- Respeta robots.txt y espacia las peticiones a cada servidor.
- Limita el tamaño de cada respuesta y solo acepta tipos de contenido útiles.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from gamepal import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"game-pal/{__version__} (+https://github.com/ruvelro/game-pal)"
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/pdf",
                 "application/xml", "text/xml")  # fmt: skip


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Page:
    url: str
    content_type: str
    body: bytes
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def normalize_url(url: str) -> str:
    """Quita el fragmento y normaliza esquema y host para no descargar dos veces lo mismo."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"No se encuentra el servidor {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            return False
    return bool(infos)


class Fetcher:
    def __init__(
        self,
        *,
        rate_per_host: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: object | None = None,
    ) -> None:
        self._interval = 1.0 / rate_per_host if rate_per_host > 0 else 0.0
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es,en;q=0.8,*;q=0.5"},
            timeout=httpx.Timeout(20, connect=10),
            follow_redirects=False,
            transport=transport,
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Para tests: función que decide si un host es público.
        self._is_public = resolver if callable(resolver) else _public

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _check_host(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise FetchError(f"Solo se admiten direcciones http(s): {url}")
        if parts.username or parts.password:
            raise FetchError("La dirección no puede llevar credenciales")
        host = parts.hostname
        public = await asyncio.to_thread(self._is_public, host)  # type: ignore[arg-type]
        if not public:
            raise FetchError(f"{host} no es una dirección pública; por seguridad no se descarga")
        return host

    async def _polite(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last.get(host, 0.0) + self._interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                page = await self._get(origin + "/robots.txt", max_bytes=512 * 1024, accept_any=True)
                assert parser is not None
                parser.parse(page.text.splitlines())
            except FetchError as exc:
                # Sin robots.txt (4xx) se permite todo; si el servidor falla (5xx) no se descarga nada.
                if exc.status is not None and exc.status >= 500:
                    parser = None
                elif parser is not None:
                    parser.parse([])
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is not None and parser.can_fetch(USER_AGENT, url)

    async def fetch(self, url: str, *, etag: str | None = None, last_modified: str | None = None) -> Page:
        if not await self.allowed(url):
            raise FetchError(f"robots.txt no permite descargar {url}")
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return await self._get(url, headers=headers)

    async def get_json(self, url: str, max_bytes: int = MAX_JSON_BYTES) -> Any:
        """Una respuesta JSON (la API de una wiki), con las mismas protecciones que una página."""
        if not await self.allowed(url):
            raise FetchError(f"robots.txt no permite descargar {url}")
        page = await self._get(url, max_bytes=max_bytes, accept_any=True)
        try:
            return json.loads(page.body)
        except ValueError as exc:
            raise FetchError(f"Respuesta no válida de {url}") from exc

    async def _get(
        self, url: str, *, headers: dict[str, str] | None = None, max_bytes: int | None = None, accept_any: bool = False
    ) -> Page:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            host = await self._check_host(current)
            await self._polite(host)
            try:
                async with self._client.stream("GET", current, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError(f"Redirección sin destino en {current}")
                        current = urljoin(current, location)
                        continue
                    if response.status_code == 304:
                        sent = headers or {}
                        return Page(current, "", b"", sent.get("If-None-Match"), sent.get("If-Modified-Since"), True)
                    if response.status_code >= 400:
                        raise FetchError(f"HTTP {response.status_code} en {current}", response.status_code)
                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if not accept_any and not content_type.startswith(ALLOWED_TYPES):
                        raise FetchError(f"Tipo de contenido no admitido ({content_type}) en {current}")
                    limit = max_bytes or (MAX_PDF_BYTES if content_type == "application/pdf" else MAX_HTML_BYTES)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > limit:
                            raise FetchError(f"{current} es demasiado grande")
                    return Page(
                        str(response.url),
                        content_type,
                        bytes(body),
                        response.headers.get("etag"),
                        response.headers.get("last-modified"),
                    )
            except httpx.HTTPError as exc:
                raise FetchError(f"No se pudo descargar {current}: {exc}") from exc
        raise FetchError(f"Demasiadas redirecciones desde {url}")
