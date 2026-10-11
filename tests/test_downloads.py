import threading
from pathlib import Path

from gamepal.downloads import MB, FolderProgress, describe


def test_folder_progress_reports_growth(tmp_path: Path) -> None:
    reports: list[tuple[int, float | None]] = []
    seen = threading.Event()

    def report(received: int, fraction: float | None) -> None:
        reports.append((received, fraction))
        seen.set()

    (tmp_path / "ya-estaba.bin").write_bytes(b"x" * (3 * MB))
    with FolderProgress(tmp_path, report, expected_mb=10, interval=0.01):
        (tmp_path / "nuevo.bin").write_bytes(b"x" * (4 * MB))
        assert seen.wait(2)
    received, fraction = reports[-1]
    # Solo cuenta lo descargado ahora, no lo que ya había.
    assert received == 4 * MB
    assert fraction == 0.4


def test_folder_progress_ignores_cached_models(tmp_path: Path) -> None:
    with FolderProgress(tmp_path, lambda *_: None, interval=0.01) as watch:
        (tmp_path / "config.json").write_text("{}")
    assert watch.downloaded == 0


def test_describe() -> None:
    assert describe("Whisper base", 70 * MB, 145) == "Descargando Whisper base: 70 de 145 MB"
    assert describe("algo", 3 * MB, None) == "Descargando algo: 3 MB"
