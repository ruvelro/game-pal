import os
from pathlib import Path

import pytest

from gamepal import checks, cli, ui_build
from gamepal.checks import Check, check_config, check_game, check_models, check_ocr, check_voice
from gamepal.config import AppConfig, OcrConfig, RoleRoute, RoutingConfig, VoiceConfig
from gamepal.explain import explain_error
from gamepal.paths import AppPaths
from gamepal.profiles import ProfileStore
from gamepal.providers import ProviderError
from tests.conftest import GAME

# Arranque --------------------------------------------------------------------------------------

COMMANDS = {"run", "doctor", "windows"}


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], ["run"]),
        (["--no-open"], ["run", "--no-open"]),
        (["-v", "--dev"], ["-v", "run", "--dev"]),
        (["doctor"], ["doctor"]),
        (["-v", "doctor", "--json"], ["-v", "doctor", "--json"]),
        (["--version"], ["--version"]),
    ],
)
def test_default_command_is_run(argv: list[str], expected: list[str]) -> None:
    assert cli.with_default_command(argv, COMMANDS) == expected


def test_top_level_run_options_parse() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(cli.with_default_command(["--no-open", "--dev"], {"run", "doctor"}))
    assert args.command == "run"
    assert args.open is False
    assert args.dev is True


def _touch(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


@pytest.fixture
def ui_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source, static = tmp_path / "ui", tmp_path / "static"
    _touch(source / "package.json", 1000)
    _touch(source / "package-lock.json", 1000)
    _touch(source / "src" / "App.tsx", 1000)
    monkeypatch.setattr(ui_build, "SOURCE_DIR", source)
    monkeypatch.setattr(ui_build, "STATIC_DIR", static)
    return source, static


def test_needs_build_when_missing_or_stale(ui_tree: tuple[Path, Path]) -> None:
    source, static = ui_tree
    assert ui_build.needs_build(source)
    _touch(static / "index.html", 2000)
    assert not ui_build.needs_build(source)
    _touch(source / "src" / "pages" / "New.tsx", 3000)
    assert ui_build.needs_build(source)


def test_needs_install_follows_lockfile(ui_tree: tuple[Path, Path]) -> None:
    source, _ = ui_tree
    assert ui_build.needs_install(source)
    _touch(source / "node_modules" / ".package-lock.json", 2000)
    assert not ui_build.needs_install(source)
    _touch(source / "package-lock.json", 3000)
    assert ui_build.needs_install(source)


def test_ensure_ui_explains_missing_node(ui_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, static = ui_tree
    monkeypatch.setattr(ui_build.shutil, "which", lambda _: None)
    assert ui_build.ensure_ui(lambda _: None) == ui_build.NODE_MISSING
    # Con una compilación anterior, se usa esa y solo se avisa.
    _touch(static / "index.html", 2000)
    _touch(source / "src" / "App.tsx", 3000)
    warning = ui_build.ensure_ui(lambda _: None)
    assert warning is not None
    assert "versión anterior" in warning


def test_ensure_ui_skips_fresh_build(ui_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _, static = ui_tree
    _touch(static / "index.html", 2000)
    monkeypatch.setattr(ui_build, "build", lambda *_: pytest.fail("no debería compilar"))
    assert ui_build.ensure_ui(lambda _: None) is None


# Comprobaciones ----------------------------------------------------------------------------------


def servers(available: dict[str, tuple[str, ...]]) -> checks.Probe:
    async def probe(url: str) -> tuple[str, ...]:
        if url not in available:
            raise ProviderError(f"No se pudo contactar con {url}")
        return available[url]

    return probe


OLLAMA = "http://127.0.0.1:11434/v1"
LM_STUDIO = "http://127.0.0.1:1234/v1"


def no_keys(_: str) -> bool:
    return False


async def test_models_without_any_server_suggests_ollama() -> None:
    check = await check_models(AppConfig(), no_keys, servers({}))
    assert check.state == "error"
    assert check.required
    assert "ningún servidor" in check.detail
    assert check.command == f"ollama pull {checks.STARTER_MODEL}"


async def test_models_points_to_another_running_server() -> None:
    check = await check_models(AppConfig(), no_keys, servers({LM_STUDIO: ("qwen",)}))
    assert check.state == "error"
    assert "LM Studio" in check.fix


async def test_models_asks_to_choose_one() -> None:
    check = await check_models(AppConfig(), no_keys, servers({OLLAMA: ("gemma3:4b", "qwen3:4b")}))
    assert check.state == "error"
    assert "no has elegido" in check.detail
    assert "qwen3:4b" in check.fix


async def test_models_offers_to_pull_missing_model() -> None:
    config = AppConfig()
    config.provider.model = "qwen3:8b"
    check = await check_models(config, no_keys, servers({OLLAMA: ("gemma3:4b",)}))
    assert check.state == "error"
    assert check.command == "ollama pull qwen3:8b"


async def test_models_ready() -> None:
    config = AppConfig()
    config.provider.model = "qwen3:4b"
    check = await check_models(config, no_keys, servers({OLLAMA: ("qwen3:4b",)}))
    assert check.state == "ok"
    assert check.detail == "qwen3:4b en Ollama"


async def test_models_in_the_cloud_need_a_key() -> None:
    config = AppConfig(routing=RoutingConfig(reasoning=RoleRoute(provider="anthropic")))
    missing = await check_models(config, no_keys, servers({}))
    assert missing.state == "error"
    assert "clave" in missing.detail
    ready = await check_models(config, lambda name: name == "anthropic", servers({}))
    assert ready.state == "ok"
    assert "Anthropic" in ready.detail


def test_game_check(repo_profiles: Path, tmp_path: Path) -> None:
    store = ProfileStore(repo_profiles, tmp_path)
    config = AppConfig()
    generic = check_game(config, store.get("generic"), None, [GAME], None)
    assert generic.state == "warn"
    assert not generic.required

    config.window_title = "Mi Juego"
    assert check_game(config, store.get("generic"), None, [GAME], None).state == "ok"

    hades = check_game(AppConfig(profile="hades-2"), store.get("hades-2"), None, [GAME], None)
    assert hades.state == "warn"
    assert "no está abierto" in hades.detail

    broken = check_game(AppConfig(profile="nope"), None, "no existe", [GAME], None)
    assert broken.state == "error"


def test_ocr_and_voice_checks() -> None:
    assert check_ocr(AppConfig(ocr=OcrConfig(backend="none")), None, None).state == "warn"
    assert check_ocr(AppConfig(), None, "falta el motor").state == "error"
    assert check_voice(AppConfig()).state == "off"

    enabled = AppConfig(voice=VoiceConfig(enabled=True))

    def broken() -> dict[str, list[str]]:
        raise OSError("PortAudio library not found")

    assert check_voice(enabled, broken).state == "error"
    assert check_voice(enabled, lambda: {"inputs": [], "outputs": []}).detail == "No encuentro ningún micrófono"
    assert check_voice(enabled, lambda: {"inputs": ["Mic"], "outputs": []}).state == "ok"


def test_invalid_config_is_explained(app_paths: AppPaths) -> None:
    app_paths.config_file.write_text("capture:\n  idle_fps: -3\n", encoding="utf-8")
    check, config = check_config(app_paths)
    assert check.state == "error"
    assert "bórrala" in check.fix
    assert config == AppConfig()


def test_doctor_reports_fixes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("GAMEPAL_HOME", str(tmp_path))
    found = [
        Check(id="gui", title="Interfaz", state="ok", detail="Compilada"),
        Check(id="model", title="Modelo de IA", state="error", detail="Nada", fix="Instala Ollama", command="ollama"),
        Check(id="voice", title="Voz", state="off", required=False, detail="Desactivada", fix="Actívala"),
    ]

    async def fake(_: AppPaths) -> list[Check]:
        return found

    monkeypatch.setattr(checks, "doctor_checks", fake)
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "Instala Ollama" in out
    assert "1 cosa impide" in out

    found[1] = found[1].model_copy(update={"state": "ok"})
    assert cli.main(["doctor"]) == 0
    assert "Todo listo" in capsys.readouterr().out


async def test_models_detects_a_busy_port() -> None:
    async def busy(url: str) -> tuple[str, ...]:
        raise ProviderError(f"local respondió 503: model inventory unavailable ({url})")

    check = await check_models(AppConfig(), no_keys, busy)
    assert check.state == "error"
    assert "otro programa" in check.detail


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("No hay modelo seleccionado para local", "Aún no has elegido el modelo"),
        ("No se pudo contactar con local: ConnectError", "No consigo hablar con Ollama"),
        ("local respondió 404: model 'qwen3:8b' not found", "ollama pull qwen3:8b"),
        # Ollama 0.40 responde 502 cuando no tiene el modelo: no es falta de memoria.
        ("local respondió 502: no available node advertises the requested model", "ollama pull qwen3:8b"),
        ('local respondió 400: model "qwen3:8b" not found, try pulling it first', "ollama pull qwen3:8b"),
        ("local: el modelo gastó todo el margen pensando y no llegó a responder", "se ha quedado pensando"),
        ("el modelo no devolvió ningún texto", "no ha devuelto ninguna respuesta"),
        ("local respondió 503: model inventory unavailable", "otro programa"),
        ("Anthropic rechazó la clave de API", "Anthropic (Claude) no acepta la clave"),
        ("openrouter respondió 429: rate limited", "límite de uso"),
        ("Se cortó la conexión con local: ReadTimeout", "ha tardado demasiado"),
        ("Algo totalmente distinto", "Algo totalmente distinto"),
    ],
)
def test_errors_are_explained(raw: str, expected: str) -> None:
    config = AppConfig()
    config.provider.model = "qwen3:8b"
    assert expected in explain_error(raw, config)
