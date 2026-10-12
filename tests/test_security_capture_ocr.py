import pytest

from gamepal.capture.base import WindowInfo, downscale, match_window
from gamepal.events import Box, TextLine
from gamepal.perception.ocr.base import normalized_box, reading_order
from gamepal.security import (
    UnsafeUrlError,
    allowed_host_header,
    tokens_match,
    validate_local_endpoint,
    validate_model_server,
    validate_remote_endpoint,
)
from tests.conftest import pattern


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:11434/v1", "http://localhost:1234/v1/", "http://[::1]:8080/v1", "https://127.0.0.5/v1"]
)
def test_local_endpoints_accept_loopback(url: str) -> None:
    assert not validate_local_endpoint(url).endswith("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:11434/v1",
        "http://example.com/v1",
        "http://user:pass@127.0.0.1/v1",
        "http://127.0.0.1/v1?x=1",
        "file:///etc/passwd",
        "http://127.0.0.1.evil.com/v1",
    ],
)
def test_local_endpoints_reject_everything_else(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_local_endpoint(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://192.168.1.20:11434/v1",
        "http://10.0.0.7:1234/v1",
        "http://172.16.4.2:8080/v1",
        "http://100.101.102.103:11434/v1",  # Tailscale
        "http://[fd12:3456::1]:11434/v1",
        "http://mi-pc:11434/v1",
        "http://torre.local:1234/v1",
        "http://gpu.tail1234.ts.net:11434/v1",
        "https://modelos.example.com/v1",
    ],
)
def test_model_servers_on_the_home_network_or_https(url: str) -> None:
    assert validate_model_server(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://modelos.example.com/v1",
        "http://8.8.8.8:11434/v1",
        "http://user:pass@192.168.1.20/v1",
        "http://192.168.1.20/v1?x=1",
        "ftp://192.168.1.20/v1",
        "http://0.0.0.0:11434/v1",
        "http://192.168.1.20.evil.com/v1",
    ],
)
def test_model_servers_reject_the_rest(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_model_server(url)


def test_remote_endpoints_require_https() -> None:
    assert validate_remote_endpoint("https://api.example.com/v1/") == "https://api.example.com/v1"
    with pytest.raises(UnsafeUrlError):
        validate_remote_endpoint("http://api.example.com/v1")


def test_host_header_and_tokens() -> None:
    assert allowed_host_header("127.0.0.1:5000", 5000)
    assert allowed_host_header("LOCALHOST:5000", 5000)
    assert not allowed_host_header("evil.com:5000", 5000)
    assert not allowed_host_header("127.0.0.1:5001", 5000)
    assert not allowed_host_header(None, 5000)
    assert tokens_match("abc", "abc")
    assert not tokens_match("abc", "abd")
    assert not tokens_match("abc", None)


def _w(id: int, process: str, title: str, pid: int, w: int = 800, h: int = 600) -> WindowInfo:
    return WindowInfo(id=id, title=title, process=process, pid=pid, width=w, height=h)


def test_match_window_by_process_ignores_extension_and_case() -> None:
    windows = [_w(1, "Navegador", "Hades II wiki", 1), _w(2, r"C:\Games\Hades2.exe", "Hades II", 2)]
    assert match_window(windows, ["hades2"], None) == windows[1]


def test_match_window_by_title_prefers_foreground_then_size() -> None:
    windows = [_w(1, "a", "Stardew Valley", 1, 1920, 1080), _w(2, "b", "Stardew Valley", 2, 800, 600)]
    assert match_window(windows, [], "stardew", foreground_pid=2) == windows[1]
    assert match_window(windows, [], "stardew") == windows[0]


def test_match_window_skips_tiny_windows_and_empty_targets() -> None:
    windows = [_w(1, "game", "Game", 1, 100, 50)]
    assert match_window(windows, ["game"], None) is None
    assert match_window(windows, [], None) is None


def test_downscale_keeps_aspect() -> None:
    image = pattern(1, 3840, 2160)
    small = downscale(image, 1920)
    assert small.shape == (1080, 1920, 3)
    assert downscale(small, 1920) is small


def test_normalized_box_clamps() -> None:
    box = normalized_box([[-5, 10], [110, 10], [110, 30], [-5, 30]], 100, 100)
    assert box == Box(x=0.0, y=0.1, width=1.0, height=0.2)


def test_reading_order_groups_rows() -> None:
    def line(text: str, x: float, y: float) -> TextLine:
        return TextLine(text=text, confidence=1, box=Box(x=x, y=y, width=0.1, height=0.04))

    lines = [line("C", 0.1, 0.5), line("B", 0.6, 0.105), line("A", 0.1, 0.1)]
    assert [item.text for item in reading_order(lines)] == ["A", "B", "C"]
