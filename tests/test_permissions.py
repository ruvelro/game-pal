from pathlib import Path

from gamepal.config import AppConfig, save_config
from gamepal.daemon import Daemon
from gamepal.keystore import MemorySecretStore
from gamepal.paths import AppPaths
from gamepal.permissions import Need, needed
from tests.conftest import FakeBackend


def test_screen_is_always_needed_and_the_rest_only_when_used() -> None:
    quiet = AppConfig.model_validate(
        {"output": {"proactive": {"mute_key": ""}, "overlay": {"chat_key": ""}}, "voice": {"enabled": False}}
    )
    assert needed(quiet) == {"screen"}
    # Por defecto hay atajos (silenciar avisos y el chat): hace falta escuchar el teclado.
    assert needed(AppConfig()) == {"screen", "input"}
    talking = AppConfig.model_validate({"voice": {"enabled": True}})
    assert needed(talking) == {"screen", "input", "microphone"}


async def test_daemon_asks_once_and_again_only_for_new_needs(app_paths: AppPaths, repo_profiles: Path) -> None:
    save_config(AppConfig(), app_paths.config_file)
    asked: list[set[Need]] = []
    daemon = Daemon(
        app_paths,
        backend_factory=FakeBackend,
        bundled_dir=repo_profiles,
        secrets=MemorySecretStore(),
        permissions=asked.append,
    )
    await daemon.ask_permissions()
    await daemon.ask_permissions()
    assert asked == [{"screen", "input"}]
    # Activar la voz pide el micrófono, y solo eso.
    daemon.config = AppConfig.model_validate({"voice": {"enabled": True}})
    await daemon.ask_permissions()
    assert asked == [{"screen", "input"}, {"microphone"}]
    daemon.activity.close()


async def test_without_a_way_to_ask_nothing_is_asked(app_paths: AppPaths, repo_profiles: Path) -> None:
    daemon = Daemon(app_paths, backend_factory=FakeBackend, bundled_dir=repo_profiles, secrets=MemorySecretStore())
    await daemon.ask_permissions()
    daemon.activity.close()
