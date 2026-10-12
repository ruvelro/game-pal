"""Servidor llama.cpp gestionado por game-pal.

- Se instala desde una versión fijada de llama.cpp y se verifica por SHA-256 antes de descomprimir.
- Arranca en modo router: un solo proceso sirve todos los modelos del catálogo instalados y los
  carga cuando se piden, hasta un máximo a la vez.
- Escucha solo en 127.0.0.1, en un puerto libre y con una clave aleatoria por arranque que viaja
  por variable de entorno (no aparece en la lista de procesos). Funciona sin conexión.
- El entorno del proceso se limpia: no hereda claves de API ni otras variables del usuario.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from gamepal.runtime.catalog import Catalog, ModelEntry, RuntimeVariant
from gamepal.runtime.downloads import download, present
from gamepal.security import new_token

log = logging.getLogger(__name__)

MAX_EXTRACTED_BYTES = 2 * 1024**3
START_TIMEOUT_S = 60
STOP_TIMEOUT_S = 10
ENV_KEEP = ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL")

State = Literal["missing", "stopped", "starting", "running", "error"]


class EngineError(RuntimeError):
    pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _inside(root: Path, target: Path) -> bool:
    return target.resolve().is_relative_to(root.resolve())


def extract(archive: Path, dest: Path) -> None:
    """Descomprime sin dejar salir nada de `dest` y con un límite de tamaño."""
    dest.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            total = sum(info.file_size for info in bundle.infolist())
            if total > MAX_EXTRACTED_BYTES:
                raise EngineError("El paquete del motor es demasiado grande")
            for info in bundle.infolist():
                if not _inside(dest, dest / info.filename):
                    raise EngineError(f"Ruta no permitida en el paquete: {info.filename}")
            bundle.extractall(dest)  # noqa: S202 - todas las rutas comprobadas arriba
        return
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if sum(m.size for m in members) > MAX_EXTRACTED_BYTES:
            raise EngineError("El paquete del motor es demasiado grande")
        # El filtro «data» rechaza rutas absolutas, «..», dispositivos y enlaces que salgan de dest.
        bundle.extractall(dest, filter="data")


@dataclass(slots=True)
class RunningServer:
    process: asyncio.subprocess.Process
    port: int
    key: str
    models: list[str]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


class LlamaRuntime:
    def __init__(self, root: Path, log_dir: Path, catalog: Catalog) -> None:
        self.root = root
        self.log_dir = log_dir
        self.catalog = catalog
        self.models_dir = root / "models"
        self._server: RunningServer | None = None
        self.state: State = "stopped"
        self.detail = ""
        self._lock = asyncio.Lock()
        self._lifeline: Any = None

    # Instalación ----------------------------------------------------------------------------------

    def runtime_dir(self, variant: RuntimeVariant) -> Path:
        return self.root / "llama.cpp" / self.catalog.runtime.tag / variant.id

    def server_binary(self, variant: RuntimeVariant) -> Path | None:
        folder = self.runtime_dir(variant)
        marker = folder / "installed.json"
        if not marker.is_file():
            return None
        name = "llama-server.exe" if variant.os == "windows" else "llama-server"
        found = next((p for p in folder.rglob(name) if p.is_file()), None)
        return found

    def install_runtime(
        self, variant: RuntimeVariant, progress: Callable[[int, int], None] | None, stop: threading.Event | None
    ) -> Path:
        archive = self.root / "downloads" / variant.asset
        download(
            self.catalog.runtime.asset_url(variant), archive, size=variant.size, sha256=variant.sha256,
            progress=progress, stop=stop,
        )  # fmt: skip
        final = self.runtime_dir(variant)
        final.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=final.parent))
        try:
            extract(archive, staging)
            name = "llama-server.exe" if variant.os == "windows" else "llama-server"
            binary = next((p for p in staging.rglob(name) if p.is_file()), None)
            if binary is None:
                raise EngineError("El paquete no contiene llama-server")
            if os.name == "posix":
                for item in binary.parent.iterdir():
                    if item.is_file() and not item.suffix:
                        item.chmod(0o755)
            (staging / "installed.json").write_text(
                json.dumps({"tag": self.catalog.runtime.tag, "variant": variant.id, "sha256": variant.sha256}),
                encoding="utf-8",
            )
            if final.exists():
                shutil.rmtree(final)
            staging.rename(final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        archive.unlink(missing_ok=True)
        return final

    def model_path(self, entry: ModelEntry, index: int) -> Path:
        return self.models_dir / entry.id / entry.files[index].path

    def model_installed(self, entry: ModelEntry) -> bool:
        return all(present(self.model_path(entry, i), f.size) for i, f in enumerate(entry.files))

    def install_model(
        self, entry: ModelEntry, progress: Callable[[int, int], None] | None, stop: threading.Event | None
    ) -> None:
        done = 0
        for index, item in enumerate(entry.files):
            offset = done

            def report(received: int, _total: int, offset: int = offset) -> None:
                if progress is not None:
                    progress(offset + received, entry.size)

            download(item.url, self.model_path(entry, index), size=item.size, sha256=item.sha256,
                     progress=report, stop=stop)  # fmt: skip
            done += item.size

    def remove_model(self, entry: ModelEntry) -> None:
        folder = self.models_dir / entry.id
        if folder.exists() and _inside(self.models_dir, folder):
            shutil.rmtree(folder)

    def installed_models(self) -> list[ModelEntry]:
        return [m for m in self.catalog.models if self.model_installed(m)]

    # Servidor -------------------------------------------------------------------------------------

    def write_preset(self, models: list[ModelEntry], *, context: int, preload: str | None) -> Path:
        lines = ["version = 1", "", "[*]", f"c = {context}", "reasoning = off", "jinja = true", ""]
        for entry in models:
            lines.append(f"[{entry.id}]")
            for index, item in enumerate(entry.files):
                key = "mmproj" if item.kind == "mmproj" else "model"
                lines.append(f"{key} = {self.model_path(entry, index).resolve()}")
            if entry.id == preload:
                lines.append("load-on-startup = true")
            lines.append("")
        path = self.root / "models.ini"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    @property
    def server(self) -> RunningServer | None:
        return self._server if self.state == "running" else None

    def status(self) -> dict[str, Any]:
        server = self.server
        return {
            "state": self.state,
            "detail": self.detail,
            "port": server.port if server else None,
            "models": server.models if server else [],
            "tag": self.catalog.runtime.tag,
        }

    async def start(
        self, variant: RuntimeVariant, models: list[ModelEntry], *, context: int, models_max: int, preload: str | None
    ) -> RunningServer:
        async with self._lock:
            await self._stop_locked()
            binary = self.server_binary(variant)
            if binary is None:
                self.state, self.detail = "missing", "El motor no está instalado"
                raise EngineError(self.detail)
            if not models:
                self.state, self.detail = "stopped", "No hay modelos instalados"
                raise EngineError(self.detail)
            await asyncio.to_thread(self._kill_orphan)
            self.state, self.detail = "starting", ""
            preset = self.write_preset(models, context=context, preload=preload)
            port, key = free_port(), new_token()
            cache = self.root / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            env = {k: v for k, v in os.environ.items() if k in ENV_KEEP}
            env.update({"LLAMA_API_KEY": key, "LLAMA_CACHE": str(cache)})
            if sys.platform.startswith("linux"):
                env["LD_LIBRARY_PATH"] = str(binary.parent)
            args = [
                str(binary), "--host", "127.0.0.1", "--port", str(port), "--models-preset", str(preset),
                "--models-max", str(models_max), "--offline", "--no-webui",
            ]  # fmt: skip
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_file = (self.log_dir / "llama-server.log").open("wb")
            kwargs: dict[str, Any] = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            try:
                process = await asyncio.create_subprocess_exec(
                    *args, cwd=str(binary.parent), env=env, stdin=asyncio.subprocess.DEVNULL,
                    stdout=log_file, stderr=subprocess.STDOUT, **kwargs,
                )  # fmt: skip
            finally:
                log_file.close()
            (self.root / "server.pid").write_text(str(process.pid), encoding="utf-8")
            self._lifeline = _tie_to_parent(process.pid)
            server = RunningServer(process, port, key, [m.id for m in models])
            self._server = server
            try:
                await self._wait_ready(server)
            except Exception as exc:
                await self._stop_locked()
                self.state, self.detail = "error", f"{exc}. {self.log_tail()}".strip()
                raise EngineError(self.detail) from exc
            self.state, self.detail = "running", ""
            log.info("Motor local en el puerto %d con %d modelos", port, len(models))
            return server

    async def _wait_ready(self, server: RunningServer) -> None:
        url = f"http://127.0.0.1:{server.port}/health"
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            for _ in range(START_TIMEOUT_S * 4):
                if server.process.returncode is not None:
                    raise EngineError(f"El motor se cerró al arrancar (código {server.process.returncode})")
                with contextlib.suppress(httpx.HTTPError):
                    response = await client.get(url)
                    if response.status_code == 200:
                        return
                await asyncio.sleep(0.25)
        raise EngineError("El motor no respondió a tiempo")

    def log_tail(self, lines: int = 8) -> str:
        try:
            text = (self.log_dir / "llama-server.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.strip().splitlines()[-lines:])

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()
            if self.state != "missing":
                self.state, self.detail = "stopped", ""

    async def _stop_locked(self) -> None:
        server, self._server = self._server, None
        if server is None or server.process.returncode is not None:
            return
        process = server.process
        try:
            if sys.platform == "win32":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return
        try:
            await asyncio.wait_for(process.wait(), STOP_TIMEOUT_S)
        except TimeoutError:
            await asyncio.to_thread(_kill_tree, process.pid)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), STOP_TIMEOUT_S)
        (self.root / "server.pid").unlink(missing_ok=True)
        await asyncio.to_thread(self._release_lifeline)

    def _release_lifeline(self) -> None:
        lifeline, self._lifeline = self._lifeline, None
        if isinstance(lifeline, subprocess.Popen):
            with contextlib.suppress(subprocess.TimeoutExpired):
                lifeline.wait(timeout=5)  # el vigía sale solo al ver que el servidor terminó
        elif lifeline is not None and sys.platform == "win32":
            import ctypes

            ctypes.WinDLL("kernel32").CloseHandle(lifeline)

    def _kill_orphan(self) -> None:
        """Si game-pal se cerró de golpe, el servidor anterior puede seguir vivo: se cierra."""
        pidfile = self.root / "server.pid"
        try:
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError):
            return
        pidfile.unlink(missing_ok=True)
        if _is_llama_server(pid):
            log.info("Cerrando un motor local que quedó abierto (pid %d)", pid)
            _kill_tree(pid)


# Vigía en POSIX: si game-pal muere de golpe (cuelgue, SIGKILL), cierra el grupo del servidor.
# Sale solo cuando el servidor termina por su cuenta.
WATCHDOG = (
    'while kill -0 "$1" 2>/dev/null && kill -0 "$2" 2>/dev/null; do sleep 2; done; '
    'if kill -0 "$2" 2>/dev/null; then kill -TERM -"$2"; sleep 10; kill -KILL -"$2" 2>/dev/null; fi'
)


def _tie_to_parent(pid: int) -> Any:
    """Hace que el servidor y sus modelos mueran con game-pal aunque este no se cierre bien."""
    try:
        if sys.platform == "win32":
            return _windows_job(pid)
        return subprocess.Popen(  # noqa: S603 - /bin/sh con un guion fijo y dos números
            ["/bin/sh", "-c", WATCHDOG, "game-pal-watchdog", str(os.getpid()), str(pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )  # fmt: skip
    except OSError:
        log.warning("No se pudo vigilar el motor local", exc_info=True)
        return None


def _windows_job(pid: int) -> Any:
    """Trabajo de Windows con «matar al cerrar»: el sistema cierra el motor y sus hijos si game-pal muere."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]  # fmt: skip

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject")
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
    handle = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess")
    try:
        if not kernel32.AssignProcessToJobObject(job, handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
    finally:
        kernel32.CloseHandle(handle)
    return job  # el asa se guarda mientras el motor vive; al morir game-pal, Windows cierra el trabajo


def _system_tool(name: str) -> str:
    """Ruta absoluta de una herramienta del sistema, para no depender del PATH."""
    if sys.platform == "win32":
        return str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", f"{name}.exe"))
    return f"/bin/{name}"


def _is_llama_server(pid: int) -> bool:
    if sys.platform == "win32":
        args = [_system_tool("tasklist"), "/FI", f"PID eq {pid}", "/NH"]
    else:
        args = [_system_tool("ps"), "-o", "command=", "-p", str(pid)]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False).stdout  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return False
    return "llama-server" in out


def _kill_tree(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603
                [_system_tool("taskkill"), "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=10, check=False
            )
        else:
            os.killpg(pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
