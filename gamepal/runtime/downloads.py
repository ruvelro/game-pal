"""Descargas grandes, reanudables y verificadas.

- Solo HTTPS y solo desde hosts permitidos (las redirecciones de sus CDN pueden ir a otro host HTTPS).
- Se descarga a `<fichero>.part` y se reanuda con Range si se cortó. El fichero final solo aparece
  cuando el tamaño y el SHA-256 casan; si no, se borra la parte y se informa.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ALLOWED_HOSTS = frozenset({"huggingface.co", "github.com"})
CHUNK = 1 << 20

Progress = Callable[[int, int], None]


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    pass


def sha256_file(path: Path, stop: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK * 4):
            if stop is not None and stop.is_set():
                raise DownloadCancelled("Cancelado")
            digest.update(block)
    return digest.hexdigest()


def present(path: Path, size: int) -> bool:
    """Comprobación rápida por tamaño; el hash ya se verificó al terminar la descarga."""
    return path.is_file() and path.stat().st_size == size


def download(
    url: str,
    dest: Path,
    *,
    size: int,
    sha256: str,
    progress: Progress | None = None,
    stop: threading.Event | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise DownloadError(f"Origen no permitido: {url}")
    if present(dest, size):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    if have > size:
        part.unlink()
        have = 0
    digest = hashlib.sha256()
    if have:
        with part.open("rb") as handle:
            while block := handle.read(CHUNK * 4):
                digest.update(block)
    headers = {"Range": f"bytes={have}-"} if have else {}
    timeout = httpx.Timeout(60, connect=15)
    with (
        httpx.Client(follow_redirects=True, timeout=timeout, transport=transport, trust_env=True) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        if response.url.scheme != "https":
            raise DownloadError("Redirección insegura")
        if have and response.status_code == 200:
            # El servidor no admite reanudar: se empieza de cero.
            have = 0
            digest = hashlib.sha256()
        elif response.status_code not in (200, 206):
            raise DownloadError(f"El servidor respondió {response.status_code}")
        received = have
        with part.open("ab" if have else "wb") as out:
            for chunk in response.iter_bytes(CHUNK):
                if stop is not None and stop.is_set():
                    raise DownloadCancelled("Descarga cancelada")
                received += len(chunk)
                if received > size:
                    raise DownloadError("El fichero es más grande de lo esperado")
                out.write(chunk)
                digest.update(chunk)
                if progress is not None:
                    progress(received, size)
    if received != size:
        raise DownloadError(f"Descarga incompleta ({received} de {size} bytes); vuelve a intentarlo para reanudar")
    if digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise DownloadError("El fichero descargado no coincide con su huella SHA-256; se ha descartado")
    os.replace(part, dest)
    return dest
