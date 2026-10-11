"""Compilación de la GUI desde el repositorio.

Al arrancar desde el código fuente, game-pal compila la GUI si falta o si sus fuentes han cambiado, así
basta con `uv run game-pal`. Instalado como paquete, la GUI ya viene compilada dentro.

Solo usa la biblioteca estándar: el hook de empaquetado (scripts/hatch_build.py) lo carga sin dependencias.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = PACKAGE_DIR.parent / "ui"
STATIC_DIR = PACKAGE_DIR / "gui_server" / "static"
MIN_NODE = 22
_TIMEOUT_S = 900

NODE_MISSING = (
    "Para compilar la interfaz hace falta Node.js 22 o superior (https://nodejs.org). "
    "Instálalo y vuelve a arrancar game-pal: la compilará solo."
)


class UiBuildError(RuntimeError):
    pass


def source_dir() -> Path | None:
    """La carpeta ui/ del repositorio, o None si game-pal está instalado como paquete."""
    return SOURCE_DIR if (SOURCE_DIR / "package.json").is_file() else None


def _inputs(source: Path) -> Iterator[Path]:
    for name in ("index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json"):
        path = source / name
        if path.is_file():
            yield path
    for folder in ("src", "public"):
        yield from (p for p in (source / folder).rglob("*") if p.is_file())


def needs_build(source: Path, static: Path | None = None) -> bool:
    index = (static or STATIC_DIR) / "index.html"
    if not index.is_file():
        return True
    built = index.stat().st_mtime
    return any(path.stat().st_mtime > built for path in _inputs(source))


def needs_install(source: Path) -> bool:
    """npm deja node_modules/.package-lock.json al instalar: si es más viejo que el lockfile, hay que reinstalar."""
    installed = source / "node_modules" / ".package-lock.json"
    lock = source / "package-lock.json"
    return not installed.is_file() or (lock.is_file() and lock.stat().st_mtime > installed.stat().st_mtime)


def find_node() -> tuple[str, str]:
    """Rutas de node y npm, comprobando la versión mínima."""
    node, npm = shutil.which("node"), shutil.which("npm")
    if node is None or npm is None:
        raise UiBuildError(NODE_MISSING)
    version = _run([node, "--version"], Path.cwd()).strip()
    match = re.match(r"v?(\d+)", version)
    if match and int(match.group(1)) < MIN_NODE:
        raise UiBuildError(
            f"Tienes Node.js {version} y la interfaz necesita la {MIN_NODE} o superior (https://nodejs.org)."
        )
    return node, npm


def _run(command: list[str], cwd: Path) -> str:
    try:
        done = subprocess.run(  # noqa: S603 - órdenes fijas, sin shell
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_S,
            env={**os.environ, "NO_UPDATE_NOTIFIER": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UiBuildError(f"No se pudo ejecutar {Path(command[0]).stem}: {exc}") from exc
    if done.returncode != 0:
        tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-15:])
        raise UiBuildError(f"Falló «{Path(command[0]).stem} {' '.join(command[1:])}»:\n{tail}")
    return done.stdout


def install(source: Path, log: Callable[[str], None]) -> tuple[str, str]:
    """Instala las dependencias de la GUI si faltan o han cambiado. Devuelve las rutas de node y npm."""
    node, npm = find_node()
    if needs_install(source):
        log("Instalando las dependencias de la interfaz…")
        _run([npm, "ci", "--no-audit", "--no-fund"], source)
    return node, npm


def build(source: Path, log: Callable[[str], None]) -> None:
    _, npm = install(source, log)
    log("Compilando la interfaz…")
    _run([npm, "run", "build"], source)


def ensure_ui(log: Callable[[str], None]) -> str | None:
    """Compila la GUI si hace falta. Devuelve un aviso para el jugador si no se pudo."""
    source = source_dir()
    if source is None or not needs_build(source):
        return None
    had_build = (STATIC_DIR / "index.html").is_file()
    try:
        build(source, log)
    except UiBuildError as exc:
        if had_build:
            return f"No se pudo actualizar la interfaz; se usa la versión anterior. {exc}"
        return str(exc)
    return None


def start_dev_server(source: Path, *, daemon_port: int, port: int) -> subprocess.Popen[bytes]:
    """Vite con recarga en caliente, redirigiendo /api al daemon en `daemon_port`."""
    node, _ = find_node()
    vite = source / "node_modules" / "vite" / "bin" / "vite.js"
    return subprocess.Popen(  # noqa: S603 - órdenes fijas, sin shell
        [node, str(vite), "--port", str(port), "--strictPort", "--clearScreen", "false", "--logLevel", "warn"],
        cwd=source,
        env={**os.environ, "GAMEPAL_DAEMON_PORT": str(daemon_port)},
    )


def stop_dev_server(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
