from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gamepal.config import AppConfig, OcrConfig, save_config
from gamepal.daemon import Daemon
from gamepal.gui_server import create_app
from gamepal.paths import AppPaths
from tests.conftest import FakeBackend

PORT = 47999
TOKEN = "secreto-de-prueba"
HOST = {"host": f"127.0.0.1:{PORT}"}
AUTH = {**HOST, "authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(app_paths: AppPaths, repo_profiles: Path) -> Iterator[TestClient]:
    save_config(AppConfig(ocr=OcrConfig(backend="none")), app_paths.config_file)
    daemon = Daemon(app_paths, backend_factory=FakeBackend, bundled_dir=repo_profiles)
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
    assert {p["id"] for p in profiles["profiles"]} == {"generic", "hades-2"}
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
