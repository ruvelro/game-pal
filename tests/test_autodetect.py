from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gamepal.autodetect import choose_local_model, detect_game, model_size_b, next_profile, pick_model
from gamepal.capture import WindowInfo
from gamepal.checks import Probe
from gamepal.config import AppConfig, KnowledgeConfig, OcrConfig, OutputConfig, OverlayConfig, load_config, save_config
from gamepal.daemon import Daemon
from gamepal.gui_server import create_app
from gamepal.hardware import HardwareInfo
from gamepal.keystore import MemorySecretStore
from gamepal.paths import AppPaths
from gamepal.profiles import LoadedProfile, ProfileStore
from gamepal.providers import ProviderError
from tests.conftest import GAME, FakeBackend

OLLAMA = "http://127.0.0.1:11434/v1"
LM_STUDIO = "http://127.0.0.1:1234/v1"
STARDEW = WindowInfo(id=9, title="Stardew Valley", process="Stardew Valley.exe", pid=99, width=1280, height=720)


def servers(available: dict[str, tuple[str, ...]]) -> Probe:
    async def probe(url: str) -> tuple[str, ...]:
        if url not in available:
            raise ProviderError(f"No se pudo contactar con {url}")
        return available[url]

    return probe


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("qwen3:4b", 4.0),
        ("Llama-3.1-8B-Instruct", 8.0),
        ("gemma3:27b", 27.0),
        ("qwen3-30b-a3b", 30.0),
        ("mistral:7b-instruct-q4_K_M", 7.0),
        ("gemma3n:e2b", None),
        ("phi4", None),
        ("qwen2.5:1.5b", 1.5),
    ],
)
def test_model_size_from_name(name: str, size: float | None) -> None:
    assert model_size_b(name) == size


def test_pick_model_skips_embeddings_and_fits_memory() -> None:
    models = ("nomic-embed-text", "qwen3:4b", "qwen3:8b", "qwen3:32b", "bge-m3")
    assert pick_model(models, memory_gb=8) == "qwen3:4b"
    # Con memoria de sobra no pasa del tamaño cómodo para jugar.
    assert pick_model(models, memory_gb=64) == "qwen3:8b"
    assert pick_model(("nomic-embed-text",), memory_gb=16) is None
    # Si nada cabe, el más pequeño; si no se sabe el tamaño, cualquiera antes que nada.
    assert pick_model(("llama3.3:70b", "qwen3:32b"), memory_gb=8) == "qwen3:32b"
    assert pick_model(("phi4", "llama3.3:70b"), memory_gb=8) == "phi4"


async def test_choose_local_model_only_fills_gaps() -> None:
    choice = await choose_local_model(AppConfig(), 16, servers({OLLAMA: ("qwen3:8b",)}))
    assert choice is not None
    assert (choice.server, choice.url, choice.model) == ("Ollama", OLLAMA, "qwen3:8b")

    chosen = AppConfig()
    chosen.provider.model = "mi-modelo"
    assert await choose_local_model(chosen, 16, servers({OLLAMA: ("qwen3:8b",)})) is None


async def test_choose_local_model_finds_another_server() -> None:
    choice = await choose_local_model(AppConfig(), 16, servers({LM_STUDIO: ("qwen/qwen3-4b",)}))
    assert choice is not None
    assert (choice.server, choice.url) == ("LM Studio", LM_STUDIO)
    assert await choose_local_model(AppConfig(), 16, servers({})) is None


@pytest.fixture
def profiles(repo_profiles: Path, tmp_path: Path) -> dict[str, LoadedProfile]:
    found, _ = ProfileStore(repo_profiles, tmp_path).discover()
    return found


def test_detects_the_open_game(profiles: dict[str, LoadedProfile]) -> None:
    assert detect_game(profiles, [GAME, STARDEW], GAME.pid) == "stardew-valley"
    assert detect_game(profiles, [GAME], GAME.pid) is None


def test_next_profile_respects_manual_choices(profiles: dict[str, LoadedProfile]) -> None:
    windows = [GAME, STARDEW]
    config = AppConfig()
    assert next_profile(config, profiles["generic"], profiles, windows, None) == "stardew-valley"
    assert next_profile(config.model_copy(update={"auto_detect_game": False}), None, profiles, windows, None) is None
    assert next_profile(config.model_copy(update={"window_title": "Mi Juego"}), None, profiles, windows, None) is None
    # Si el juego del perfil activo está abierto, se queda.
    hades = WindowInfo(id=3, title="Hades II", process="Hades2.exe", pid=5, width=1920, height=1080)
    in_hades = config.model_copy(update={"profile": "hades-2"})
    assert next_profile(in_hades, profiles["hades-2"], profiles, [hades, STARDEW], hades.pid) is None


@pytest.fixture
def running(app_paths: AppPaths, repo_profiles: Path) -> Iterator[tuple[TestClient, Daemon]]:
    quiet = OutputConfig(answers=["text"], overlay=OverlayConfig(enabled=False))
    config = AppConfig(ocr=OcrConfig(backend="none"), output=quiet).model_copy(
        update={"knowledge": KnowledgeConfig(embeddings="none")}
    )
    save_config(config, app_paths.config_file)
    daemon = Daemon(
        app_paths,
        backend_factory=FakeBackend,
        bundled_dir=repo_profiles,
        secrets=MemorySecretStore(),
        hardware=lambda: HardwareInfo("linux", "x64", "cpu", 8, 16.0, [], vulkan=False),
        probe=servers({OLLAMA: ("nomic-embed-text", "qwen3:4b")}),
    )
    with TestClient(create_app(daemon, "t", 47998), base_url="http://127.0.0.1:47998") as test_client:
        yield test_client, daemon


AUTH = {"host": "127.0.0.1:47998", "authorization": "Bearer t"}


def test_daemon_picks_a_model_on_start(running: tuple[TestClient, Daemon], app_paths: AppPaths) -> None:
    client, _ = running
    assert load_config(app_paths.config_file).provider.model == "qwen3:4b"
    setup = client.get("/api/setup", headers=AUTH).json()
    assert setup["notes"] == ["Modelo elegido automáticamente: qwen3:4b en Ollama"]
    servers_found = client.get("/api/providers/local/discover", headers=AUTH).json()["servers"]
    assert servers_found == [
        {"name": "Ollama", "url": OLLAMA, "models": ["nomic-embed-text", "qwen3:4b"], "recommended": "qwen3:4b"}
    ]


def test_autodetect_switches_to_the_open_game(running: tuple[TestClient, Daemon], app_paths: AppPaths) -> None:
    client, daemon = running
    assert isinstance(daemon.c.backend, FakeBackend)
    daemon.c.backend.windows = [GAME, STARDEW]
    changes = client.post("/api/setup/autodetect", headers=AUTH).json()["changes"]
    assert changes == ["Juego detectado: Stardew Valley"]
    assert load_config(app_paths.config_file).profile == "stardew-valley"
    # Ya está en el perfil del juego abierto: no hay nada más que cambiar.
    assert client.post("/api/setup/autodetect", headers=AUTH).json()["changes"] == []


async def test_managed_engine_is_left_alone() -> None:
    config = AppConfig()
    config.provider.kind = "managed"
    assert await choose_local_model(config, 16, servers({OLLAMA: ("qwen3:8b",)})) is None
