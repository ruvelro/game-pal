"""Línea de comandos de game-pal."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import logging.handlers
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from gamepal import __version__, ui_build
from gamepal.paths import AppPaths, bundled_profiles_dir, default_paths

if TYPE_CHECKING:
    from gamepal.checks import Check
    from gamepal.daemon import Daemon

log = logging.getLogger("gamepal")

DEV_PORT = 5173
HELP_URL = "https://github.com/ruvelro/game-pal/blob/main/docs/problemas.md"


def _setup_logging(paths: AppPaths, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    file_handler = logging.handlers.RotatingFileHandler(
        paths.log_dir / "game-pal.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handlers.append(file_handler)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    # Cada petición de una descarga y cada trozo de audio de Whisper llenaban el registro y tapaban lo útil.
    for noisy in ("httpx", "httpx2", "httpcore", "huggingface_hub", "faster_whisper", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # pynput avisa de «not trusted» en cada arranque aunque el permiso de Monitorización de entrada baste;
    # game-pal ya comprueba el permiso de verdad y lo explica en «Lo que falta».
    logging.getLogger("pynput").setLevel(logging.ERROR)


def _free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


INSTANCE_FILE = "instance.json"


def _running_instance(paths: AppPaths) -> int | None:
    """Puerto de un game-pal que ya está en marcha, si lo hay."""
    import httpx

    try:
        port = int(json.loads((paths.data_dir / INSTANCE_FILE).read_text(encoding="utf-8"))["port"])
        response = httpx.get(f"http://127.0.0.1:{port}/api/instance", timeout=2, trust_env=False)
        if response.status_code == 200 and response.json().get("app") == "game-pal":
            return port
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        pass
    return None


def _show_running(port: int) -> bool:
    import httpx

    try:
        return httpx.post(f"http://127.0.0.1:{port}/api/instance/show", timeout=3, trust_env=False).is_success
    except httpx.HTTPError:
        return False


class Controller:
    """Lo que la bandeja puede hacer con el servidor, que vive en otro hilo y otro bucle."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: object | None = None
        self.daemon: object | None = None
        self.url = ""

    def call(self, action: Callable[[], object]) -> None:
        loop = self.loop
        if loop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):  # el bucle puede cerrarse justo entre medias
                loop.call_soon_threadsafe(action)


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _marks() -> dict[str, str]:
    marks = {"ok": "✓", "warn": "!", "error": "✗", "off": "·"}
    try:
        "".join(marks.values()).encode(sys.stdout.encoding or "ascii")
    except UnicodeEncodeError:
        marks = {"ok": "+", "warn": "!", "error": "x", "off": "-"}
    return marks


def _print_checks(checks: list[Check], *, stream: TextIO) -> None:
    marks = _marks()
    indent = " " * 25
    width = max(60, min(shutil.get_terminal_size().columns, 110))
    for check in checks:
        print(f"  {marks[check.state]} {check.title:<20} {check.detail}", file=stream)
        if check.fix and check.state != "ok":
            fix = textwrap.fill(check.fix, width, initial_indent=f"{indent}→ ", subsequent_indent=f"{indent}  ")
            print(fix, file=stream)
        if check.command and check.state != "ok":
            print(f"{indent}    {check.command}", file=stream)


async def _port_open(port: int) -> None:
    """Espera a que algo escuche en el puerto. uvicorn solo abre el suyo cuando el daemon ya ha arrancado."""
    while True:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.2)
            continue
        writer.close()
        return


async def _when_ready(daemon: Daemon, port: int, url: str, should_open: bool, dev_port: int | None) -> None:
    """Cuando el servidor (y Vite, en desarrollo) responde: abre la GUI y avisa de lo que falta."""
    from gamepal.checks import blocking, daemon_checks

    await _port_open(port)
    if dev_port is not None:
        try:
            async with asyncio.timeout(60):
                await _port_open(dev_port)
        except TimeoutError:
            _say("Vite no arrancó a tiempo: revisa los mensajes de arriba.")
            return
    # A stderr y nunca al fichero de registro: el token no debe quedar guardado en disco.
    _say(f"Abre la GUI con esta dirección (incluye tu token de sesión):\n  {url}")
    if should_open:
        webbrowser.open(url)
    problems = blocking(await daemon_checks(daemon))
    if problems:
        _say("\nAntes de pedir consejo, falta esto:")
        _print_checks(problems, stream=sys.stderr)
        _say("Vuelve a comprobarlo con «game-pal doctor».")


async def _serve(
    paths: AppPaths, open_browser: bool | None, dev: Path | None, controller: Controller | None = None
) -> None:
    import uvicorn

    from gamepal import permissions
    from gamepal.daemon import Daemon
    from gamepal.gui_server import create_app
    from gamepal.security import new_token

    daemon = Daemon(paths, permissions=permissions.request)
    port = _free_port(daemon.config.gui.port)
    token = new_token()
    vite: subprocess.Popen[bytes] | None = None
    dev_port: int | None = None
    if dev is not None:
        dev_port = _free_port(DEV_PORT)
        vite = ui_build.start_dev_server(dev, daemon_port=port, port=dev_port)
    url = f"http://127.0.0.1:{dev_port or port}/#token={token}"
    app = create_app(
        daemon, token, port,
        shutdown=lambda: setattr(server, "should_exit", True),
        show=lambda: webbrowser.open(url),
    )  # fmt: skip
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False, ws_max_size=65536)
    )
    if controller is not None:
        controller.loop, controller.server, controller.daemon, controller.url = (
            asyncio.get_running_loop(),
            server,
            daemon,
            url,
        )
    _say(f"game-pal {__version__}: arrancando en http://127.0.0.1:{port}…")
    if dev_port is not None:
        _say(f"Modo desarrollo: Vite en http://127.0.0.1:{dev_port}, con recarga en caliente.")
    instance = paths.data_dir / INSTANCE_FILE
    # Solo el puerto: el token nunca se escribe en disco.
    instance.write_text(json.dumps({"port": port}), encoding="utf-8")
    should_open = daemon.config.gui.open_browser if open_browser is None else open_browser
    ready = asyncio.create_task(_when_ready(daemon, port, url, should_open, dev_port))
    try:
        await server.serve()
    finally:
        ready.cancel()
        with contextlib.suppress(OSError):
            instance.unlink()
        if vite is not None:
            ui_build.stop_dev_server(vite)


def _run_with_tray(paths: AppPaths, open_browser: bool | None) -> int:
    from gamepal.tray import Tray, TrayActions

    controller = Controller()
    failure: list[BaseException] = []

    def serve() -> None:
        try:
            asyncio.run(_serve(paths, open_browser, None, controller))
        except BaseException as exc:
            failure.append(exc)
        finally:
            tray.stop()

    def daemon() -> Any:
        return controller.daemon

    def toggle_pause() -> None:
        d = daemon()
        if d is not None:
            controller.call(d.resume if d.c.watcher and d.c.watcher.paused else d.pause)

    def open_gui() -> None:
        if controller.url:
            webbrowser.open(controller.url)

    def toggle_mute() -> None:
        d = daemon()
        if d is not None:
            controller.call(d._toggle_proactive)

    tray = Tray(
        TrayActions(
            open_gui=open_gui,
            toggle_pause=toggle_pause,
            toggle_mute=toggle_mute,
            quit=lambda: controller.call(lambda: setattr(controller.server, "should_exit", True)),
            paused=lambda: bool(daemon() and daemon().c.watcher and daemon().c.watcher.paused),
            muted=lambda: bool(daemon() and daemon().assistant.proactive_muted),
        )
    )
    thread = threading.Thread(target=serve, name="game-pal-server")
    thread.start()
    tray.run()  # bloquea el hilo principal hasta Salir o hasta que el servidor termine
    if controller.server is not None:
        controller.call(lambda: setattr(controller.server, "should_exit", True))
    thread.join(timeout=30)
    if failure:
        log.error("El servidor terminó con un error", exc_info=failure[0])
        return 1
    return 0


def cmd_run(args: argparse.Namespace, paths: AppPaths) -> int:
    from gamepal.config import load_config

    try:
        load_config(paths.config_file)
    except Exception as exc:
        print(f"La configuración de {paths.config_file} no es válida:\n{exc}", file=sys.stderr)
        print("Corrígela o bórrala: game-pal creará una nueva con los valores por defecto.", file=sys.stderr)
        return 1
    port = _running_instance(paths)
    if port is not None:
        # Ya hay un game-pal abierto: se le pide que abra su GUI en lugar de arrancar otro.
        print(f"game-pal ya está en marcha en el puerto {port}; se abre su ventana.", file=sys.stderr)
        return 0 if _show_running(port) else 1
    dev: Path | None = None
    if args.dev:
        dev = ui_build.source_dir()
        if dev is None:
            print("--dev solo funciona desde el repositorio de game-pal.", file=sys.stderr)
            return 1
        try:
            ui_build.install(dev, _say)
        except ui_build.UiBuildError as exc:
            print(exc, file=sys.stderr)
            return 1
    else:
        warning = ui_build.ensure_ui(_say)
        if warning:
            _say(f"Aviso: {warning}")
    tray = args.tray if args.tray is not None else bool(getattr(sys, "frozen", False))
    if tray and dev is None:
        try:
            return _run_with_tray(paths, args.open)
        except ImportError:
            log.warning("No hay bandeja del sistema disponible; se sigue sin ella")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve(paths, args.open, dev))
    return 0


def cmd_doctor(args: argparse.Namespace, paths: AppPaths) -> int:
    from gamepal.checks import blocking, doctor_checks

    checks = asyncio.run(doctor_checks(paths))
    problems = blocking(checks)
    if args.json:
        print(json.dumps([c.model_dump() for c in checks], ensure_ascii=False, indent=2))
        return 1 if problems else 0
    print(f"game-pal {__version__} · comprobando este equipo\n")
    _print_checks(checks, stream=sys.stdout)
    print()
    if problems:
        count = "1 cosa impide" if len(problems) == 1 else f"{len(problems)} cosas impiden"
        print(f"{count} que game-pal te aconseje. Arréglalo y vuelve a comprobarlo con «game-pal doctor».")
        print(f"Más ayuda: {HELP_URL}")
        return 1
    print("Todo listo: arranca game-pal y abre la GUI.")
    return 0


def cmd_windows(args: argparse.Namespace, paths: AppPaths) -> int:
    from gamepal.capture import create_backend

    backend = create_backend()
    front = backend.foreground_pid()
    for w in backend.list_windows():
        if w.width < 200 or w.height < 150:
            continue
        mark = "*" if w.pid == front else " "
        print(f"{mark} {w.width:>5}x{w.height:<5} {w.process[:28]:<28} {w.title}")
    return 0


def cmd_capture(args: argparse.Namespace, paths: AppPaths) -> int:
    from PIL import Image

    from gamepal.capture import create_backend, match_window

    backend = create_backend()
    window = match_window(backend.list_windows(), args.process or [], args.title, backend.foreground_pid())
    if window is None:
        print("No encuentro esa ventana. Usa 'game-pal windows' para ver las disponibles.", file=sys.stderr)
        return 1
    image = backend.grab(window)
    if image is None:
        print("La ventana desapareció antes de capturarla.", file=sys.stderr)
        return 1
    Image.fromarray(image, "RGB").save(args.out)
    print(f"Guardado {args.out} ({image.shape[1]}x{image.shape[0]}) de «{window.title}»")
    return 0


def cmd_ocr(args: argparse.Namespace, paths: AppPaths) -> int:
    import time

    import numpy as np
    from PIL import Image

    from gamepal.config import OcrConfig
    from gamepal.perception.ocr import create_engine

    config = OcrConfig(backend=args.backend) if args.backend else OcrConfig()
    engine = create_engine(config, [args.lang])
    image = np.asarray(Image.open(args.image).convert("RGB"))
    started = time.perf_counter()
    lines = engine.read(image)
    elapsed = (time.perf_counter() - started) * 1000
    if args.json:
        print(json.dumps([line.model_dump() for line in lines], ensure_ascii=False, indent=2))
    else:
        for line in lines:
            b = line.box
            print(f"{line.confidence:.2f}  ({b.x:.2f},{b.y:.2f})  {line.text}")
        print(f"{len(lines)} líneas en {elapsed:.0f} ms con {engine.name}")
    return 0


def cmd_profiles(args: argparse.Namespace, paths: AppPaths) -> int:
    from gamepal.profiles import ProfileStore
    from gamepal.profiles.lint import lint

    profiles, errors = ProfileStore(bundled_profiles_dir(), paths.user_profiles_dir).discover()
    for loaded in profiles.values():
        origin = "incluido" if loaded.bundled else "usuario"
        screens = len(loaded.profile.screens)
        print(f"{loaded.profile.id:<20} {loaded.profile.name:<24} {origin:<9} {screens} pantallas")
        for warning in lint(loaded):
            print(f"    aviso: {warning}")
    for name, error in errors.items():
        print(f"\nERROR en {name}:\n{error}", file=sys.stderr)
    return 1 if errors else 0


def cmd_new_profile(args: argparse.Namespace, paths: AppPaths) -> int:
    from gamepal.profiles.scaffold import create_profile

    folder = create_profile(
        paths.user_profiles_dir, args.id, args.name or args.id.replace("-", " ").title(),
        window=args.window, process=args.process, schema=bundled_profiles_dir() / "profile.schema.json",
    )  # fmt: skip
    print(f"Perfil creado en {folder}")
    print("Edita profile.yaml y vocabulary.yaml, y compruébalo con: game-pal profiles")
    return 0


def cmd_schema(args: argparse.Namespace, paths: AppPaths) -> int:
    from gamepal.profiles import GameProfile

    schema = json.dumps(GameProfile.model_json_schema(), ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(schema + "\n", encoding="utf-8")
    else:
        print(schema)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="game-pal", description="Compañero de juego con IA, local y privado.")
    parser.add_argument("--version", action="version", version=f"game-pal {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="registro detallado")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="arranca game-pal y abre la GUI (por defecto)")
    run.add_argument("--open", dest="open", action="store_true", default=None, help="abrir el navegador")
    run.add_argument("--no-open", dest="open", action="store_false", help="no abrir el navegador")
    run.add_argument("--tray", dest="tray", action="store_true", default=None, help="icono en la bandeja del sistema")
    run.add_argument("--no-tray", dest="tray", action="store_false", help="sin icono en la bandeja")
    run.add_argument("--dev", action="store_true", help="GUI desde Vite con recarga en caliente (desarrollo)")
    run.set_defaults(func=cmd_run)

    doctor = sub.add_parser("doctor", help="comprueba que todo está listo y explica cómo arreglar lo que falte")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    sub.add_parser("windows", help="lista las ventanas que se pueden capturar").set_defaults(func=cmd_windows)

    capture = sub.add_parser("capture", help="captura una ventana una vez y la guarda en PNG")
    capture.add_argument("--title", help="subcadena del título de la ventana")
    capture.add_argument("--process", action="append", help="nombre del ejecutable (repetible)")
    capture.add_argument("--out", default="captura.png")
    capture.set_defaults(func=cmd_capture)

    ocr = sub.add_parser("ocr", help="lee el texto de una imagen")
    ocr.add_argument("image")
    ocr.add_argument("--backend", choices=["rapidocr", "apple-vision", "windows-ocr"], default=None)
    ocr.add_argument("--lang", default="en")
    ocr.add_argument("--json", action="store_true")
    ocr.set_defaults(func=cmd_ocr)

    sub.add_parser("profiles", help="lista y valida los perfiles de juego").set_defaults(func=cmd_profiles)

    new = sub.add_parser("new-profile", help="crea el esqueleto de un perfil de juego nuevo")
    new.add_argument("id", help="identificador: minúsculas, dígitos y guiones (p. ej. hollow-knight)")
    new.add_argument("--name", help="nombre del juego")
    new.add_argument("--window", help="título de su ventana")
    new.add_argument("--process", help="nombre del ejecutable, sin extensión")
    new.set_defaults(func=cmd_new_profile)

    schema = sub.add_parser("schema", help="exporta el JSON Schema de los perfiles")
    schema.add_argument("--out")
    schema.set_defaults(func=cmd_schema)
    return parser


def with_default_command(argv: list[str], commands: set[str]) -> list[str]:
    """Sin orden, se arranca: `game-pal --no-open` equivale a `game-pal run --no-open`."""
    if any(arg in commands for arg in argv) or {"-h", "--help", "--version"} & set(argv):
        return argv
    start = 0
    while start < len(argv) and argv[start] in ("-v", "--verbose"):
        start += 1
    return [*argv[:start], "run", *argv[start:]]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    args = parser.parse_args(
        with_default_command(list(sys.argv[1:] if argv is None else argv), set(subparsers.choices))
    )
    paths = default_paths()
    paths.ensure()
    _setup_logging(paths, args.verbose)
    try:
        return int(args.func(args, paths))
    except Exception as exc:
        log.debug("Error", exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return 1
