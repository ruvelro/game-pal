"""Descarga y caché de modelos de voz.

Solo se descarga de hosts permitidos y por HTTPS. Los ficheros con hash conocido se verifican;
el resto se valida por tamaño y formato. Una descarga a medias nunca sustituye a un fichero bueno.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset(
    {"huggingface.co", "github.com", "raw.githubusercontent.com", "objects.githubusercontent.com"}
)
MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024

Progress = Callable[[str, int, int | None], None]


class ModelDownloadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Artifact:
    name: str
    url: str
    sha256: str | None = None
    min_bytes: int = 1024


SILERO_VAD = Artifact(
    "silero_vad_v5.onnx",
    "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx",
    "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f",
    min_bytes=1_000_000,
)

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Voz recomendada por idioma (catálogo oficial de Piper).
PIPER_DEFAULT_VOICES = {
    "es": "es_ES-davefx-medium",
    "en": "en_US-lessac-medium",
    "fr": "fr_FR-siwis-medium",
    "de": "de_DE-thorsten-medium",
    "it": "it_IT-paola-medium",
    "pt": "pt_BR-faber-medium",
    "pl": "pl_PL-gosia-medium",
    "ru": "ru_RU-irina-medium",
    "zh": "zh_CN-huayan-medium",
    "nl": "nl_NL-mls-medium",
}


def piper_artifacts(voice: str) -> tuple[Artifact, Artifact]:
    """Ficheros de una voz de Piper a partir de su id: es_ES-davefx-medium."""
    parts = voice.split("-")
    if len(parts) != 3 or "_" not in parts[0] or not all(p.replace("_", "").isalnum() for p in parts):
        raise ModelDownloadError(f"Id de voz de Piper no válido: {voice}")
    locale, speaker, quality = parts
    folder = f"{PIPER_BASE}/{locale.split('_')[0]}/{locale}/{speaker}/{quality}"
    return (
        Artifact(f"piper/{voice}.onnx", f"{folder}/{voice}.onnx", min_bytes=1_000_000),
        Artifact(f"piper/{voice}.onnx.json", f"{folder}/{voice}.onnx.json", min_bytes=200),
    )


class ModelStore:
    def __init__(self, root: Path, transport: httpx.BaseTransport | None = None) -> None:
        self.root = root
        self._transport = transport

    def path(self, artifact: Artifact) -> Path:
        return self.root / artifact.name

    def ready(self, artifact: Artifact) -> bool:
        path = self.path(artifact)
        return path.is_file() and path.stat().st_size >= artifact.min_bytes

    def ensure(self, artifact: Artifact, progress: Progress | None = None) -> Path:
        path = self.path(artifact)
        if self.ready(artifact):
            return path
        parts = urlsplit(artifact.url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ModelDownloadError(f"Origen no permitido: {artifact.url}")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".dl-", dir=path.parent)
        tmp = Path(tmp_name)
        digest = hashlib.sha256()
        received = 0
        try:
            with (
                os.fdopen(fd, "wb") as out,
                httpx.Client(
                    follow_redirects=True, timeout=httpx.Timeout(60, connect=10), transport=self._transport
                ) as client,
                client.stream("GET", artifact.url) as response,
            ):
                if response.status_code != 200:
                    raise ModelDownloadError(f"{artifact.name}: el servidor respondió {response.status_code}")
                final = urlsplit(str(response.url))
                if final.scheme != "https":
                    raise ModelDownloadError(f"{artifact.name}: redirección insegura")
                total = int(response.headers.get("content-length", 0)) or None
                for chunk in response.iter_bytes(1 << 16):
                    received += len(chunk)
                    if received > MAX_DOWNLOAD_BYTES:
                        raise ModelDownloadError(f"{artifact.name}: descarga demasiado grande")
                    out.write(chunk)
                    digest.update(chunk)
                    if progress is not None:
                        progress(artifact.name, received, total)
            if received < artifact.min_bytes:
                raise ModelDownloadError(f"{artifact.name}: fichero incompleto ({received} bytes)")
            if artifact.sha256 and digest.hexdigest() != artifact.sha256:
                raise ModelDownloadError(f"{artifact.name}: el hash no coincide")
            if artifact.name.endswith(".json"):
                json.loads(tmp.read_text(encoding="utf-8"))
            os.replace(tmp, path)
            log.info("Modelo descargado: %s (%.1f MB)", artifact.name, received / 1e6)
            return path
        except httpx.HTTPError as exc:
            raise ModelDownloadError(f"No se pudo descargar {artifact.name}: {exc}") from exc
        except ValueError as exc:
            raise ModelDownloadError(f"{artifact.name} no es válido: {exc}") from exc
        finally:
            tmp.unlink(missing_ok=True)
