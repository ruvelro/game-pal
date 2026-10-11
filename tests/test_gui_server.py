import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gamepal.config import AppConfig, KnowledgeConfig, OcrConfig, OutputConfig, OverlayConfig, save_config
from gamepal.daemon import Daemon
from gamepal.gui_server import create_app
from gamepal.keystore import MemorySecretStore
from gamepal.paths import AppPaths
from gamepal.providers import ProviderError
from tests.conftest import FakeBackend

PORT = 47999
TOKEN = "secreto-de-prueba"
HOST = {"host": f"127.0.0.1:{PORT}"}
AUTH = {**HOST, "authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(app_paths: AppPaths, repo_profiles: Path) -> Iterator[TestClient]:
    # Sin voz ni overlay: los tests no descargan modelos ni abren ventanas.
    quiet = OutputConfig(answers=["text"], overlay=OverlayConfig(enabled=False))
    # Y sin embeddings: nada se descarga de internet durante los tests.
    config = AppConfig(ocr=OcrConfig(backend="none"), output=quiet).model_copy(
        update={"knowledge": KnowledgeConfig(embeddings="none")}
    )
    save_config(config, app_paths.config_file)
    daemon = Daemon(app_paths, backend_factory=FakeBackend, bundled_dir=repo_profiles, secrets=MemorySecretStore())
    with TestClient(create_app(daemon, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}") as test_client:
        yield test_client


def test_rejects_foreign_host_header(client: TestClient) -> None:
    response = client.get("/api/status", headers={"host": "evil.example:47999", "authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 421


def test_requires_token(client: TestClient) -> None:
    assert client.get("/api/status", headers=HOST).status_code == 401
    assert client.get("/api/status", headers={**HOST, "authorization": "Bearer otro"}).status_code == 401


def test_status_and_security_headers(client: TestClient) -> None:
    response = client.get("/api/status", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["id"] == "generic"
    assert body["capture_backend"] == "fake"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_profiles_and_windows(client: TestClient) -> None:
    profiles = client.get("/api/profiles", headers=AUTH).json()
    assert {p["id"] for p in profiles["profiles"]} == {"generic", "hades-2", "stardew-valley"}
    windows = client.get("/api/windows", headers=AUTH).json()
    assert windows[0]["title"] == "Mi Juego"


def test_config_update_validates_and_persists(client: TestClient, app_paths: AppPaths) -> None:
    config = client.get("/api/config", headers=AUTH).json()
    config["provider"]["base_url"] = "http://192.168.1.4:11434/v1"
    assert client.put("/api/config", headers=AUTH, json=config).status_code == 422

    config["provider"]["base_url"] = "http://127.0.0.1:1234/v1"
    config["window_title"] = "Mi Juego"
    response = client.put("/api/config", headers=AUTH, json=config)
    assert response.status_code == 200
    assert "Mi Juego" in app_paths.config_file.read_text(encoding="utf-8")


def test_setup_explains_what_is_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_server(url: str) -> tuple[str, ...]:
        raise ProviderError(f"No se pudo contactar con {url}")

    monkeypatch.setattr("gamepal.checks.probe_local", no_server)
    # Los permisos de macOS dependen de la máquina que ejecuta los tests.
    monkeypatch.setattr("gamepal.checks.screen_capture_allowed", lambda: None)
    assert client.get("/api/setup", headers=HOST).status_code == 401
    body = client.get("/api/setup", headers=AUTH).json()
    found = {check["id"]: check for check in body["checks"]}
    assert body["ready"] is False
    assert found["model"]["state"] == "error"
    assert found["model"]["command"].startswith("ollama pull")
    assert found["capture"]["state"] == "ok"
    assert found["voice"]["state"] == "off"


def test_pause_and_resume(client: TestClient) -> None:
    assert client.post("/api/watcher/pause", headers=AUTH).json() == {"paused": True}
    assert client.get("/api/status", headers=AUTH).json()["paused"] is True
    assert client.post("/api/watcher/resume", headers=AUTH).json() == {"paused": False}


def test_websocket_requires_token(client: TestClient) -> None:
    with client.websocket_connect("/api/events", headers=HOST) as socket:
        socket.send_json({"token": "malo"})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_websocket_rejects_foreign_origin(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/events", headers={**HOST, "origin": "https://evil.example"}) as socket,
    ):
        socket.receive_json()


def test_websocket_streams_current_state(client: TestClient) -> None:
    with client.websocket_connect("/api/events", headers=HOST) as socket:
        socket.send_json({"token": TOKEN})
        message = socket.receive_json()
        assert "topic" in message
        assert "data" in message


def test_static_paths_cannot_escape(client: TestClient) -> None:
    response = client.get("/../../pyproject.toml", headers=HOST)
    assert "game-pal" in response.text
    assert "[project]" not in response.text


def test_profile_info_and_test(client: TestClient) -> None:
    config = client.get("/api/config", headers=AUTH).json()
    config["profile"] = "hades-2"
    assert client.put("/api/config", headers=AUTH, json=config).status_code == 200
    info = client.get("/api/profile", headers=AUTH).json()
    assert info["id"] == "hades-2"
    assert info["uses_game_text"] is True
    assert info["vocabulary"]["bosses"] == 8
    assert info["warnings"] == []
    # Sin ninguna lectura todavía, la prueba lo explica en vez de fallar.
    assert client.post("/api/profile/test", headers=AUTH).status_code == 409


def test_game_dir_must_exist(client: TestClient, tmp_path: Path) -> None:
    config = client.get("/api/config", headers=AUTH).json()
    config["game_dirs"] = {"hades-2": str(tmp_path / "no-existe")}
    assert client.put("/api/config", headers=AUTH, json=config).status_code == 422
    config["game_dirs"] = {"hades-2": str(tmp_path)}
    assert client.put("/api/config", headers=AUTH, json=config).status_code == 200


def test_assistant_and_voice_endpoints(client: TestClient) -> None:
    assert len(client.post("/api/assistant/ask", headers=AUTH, json={"text": "hola"}).json()["id"]) == 12
    assert client.post("/api/assistant/ask", headers=AUTH, json={"text": ""}).status_code == 422
    # Con la voz desactivada, push-to-talk y la prueba de voz lo explican.
    assert client.post("/api/voice/ptt", headers=AUTH, json={"down": True}).status_code == 409
    assert client.post("/api/voice/say", headers=AUTH, json={"text": "hola"}).status_code == 409
    assert client.post("/api/proactive", headers=AUTH, json={"muted": True}).json() == {"muted": True}
    assert client.post("/api/overlay/test", headers=AUTH).json() == {"ok": True}
    assert "es" in client.get("/api/voice/devices", headers=AUTH).json()["voices"]


def test_knowledge_endpoints(client: TestClient) -> None:
    info = client.get("/api/knowledge", headers=AUTH).json()
    assert info["sources"] == []
    assert client.post("/api/knowledge/sources", headers=AUTH, json={"url": "ftp://x.example/a"}).status_code == 422
    guide = ("# Guía\n\n" + "Planta chirivías en primavera. " * 10).encode()
    response = client.post(
        "/api/knowledge/upload", headers=AUTH, files=[("files", ("guía.md", guide, "text/markdown"))]
    )
    assert response.status_code == 200
    bad = client.post("/api/knowledge/upload", headers=AUTH, files=[("files", ("x.exe", b"MZ", "application/x"))])
    assert bad.status_code == 422
    assert client.post("/api/knowledge/refresh", headers=AUTH, json={}).json()["queued"] == 1


def test_evidence_and_memory_endpoints(client: TestClient) -> None:
    evidence = client.get("/api/evidence", headers=AUTH).json()
    assert evidence["facts"] == []
    assert evidence["save"]["declared"] is False  # el perfil genérico no lee guardados
    assert client.post("/api/evidence/reload", headers=AUTH).status_code == 409
    assert client.post("/api/profile/trust", headers=AUTH).status_code == 409
    memory = client.get("/api/memory", headers=AUTH).json()
    assert memory["enabled"] is False
    lesson = client.post("/api/memory/lessons", headers=AUTH, json={"text": "Prefiero el hacha"}).json()
    assert client.get("/api/memory", headers=AUTH).json()["lessons"][0]["text"] == "Prefiero el hacha"
    assert client.delete(f"/api/memory/lessons/{lesson['id']}", headers=AUTH).json() == {"deleted": True}
    export = client.get("/api/memory/export", headers=AUTH)
    assert export.headers["content-disposition"].startswith("attachment")
    assert export.json()["profile"] == "generic"


def test_provider_keys_are_never_returned(client: TestClient) -> None:
    info = client.get("/api/providers", headers=AUTH).json()
    assert info["keys"] == {"openai": False, "anthropic": False, "openrouter": False}
    assert (
        client.put("/api/providers/anthropic/key", headers=AUTH, json={"key": "sk-ant-test-123456"}).status_code == 200
    )
    assert client.put("/api/providers/otro/key", headers=AUTH, json={"key": "sk-test-123456"}).status_code == 404
    assert client.put("/api/providers/openai/key", headers=AUTH, json={"key": "con espacios no"}).status_code == 422
    info = client.get("/api/providers", headers=AUTH).json()
    assert info["keys"]["anthropic"] is True
    assert "sk-ant" not in json.dumps(info)
    assert "sk-ant" not in client.get("/api/config", headers=AUTH).text
    assert client.delete("/api/providers/anthropic/key", headers=AUTH).json() == {"configured": False}


def test_activity_endpoint(client: TestClient) -> None:
    data = client.get("/api/activity", headers=AUTH).json()
    assert data["total"] == 0
    assert data["recent"] == []
