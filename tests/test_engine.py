"""Equipo, catálogo, descargas verificadas y motor local gestionado."""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import threading
import zipfile
from pathlib import Path

import httpx
import pytest

from gamepal.bus import EventBus
from gamepal.config import AppConfig, ProviderConfig
from gamepal.hardware import Gpu, HardwareInfo, parse_nvidia_smi, propose_tier
from gamepal.runtime.catalog import Catalog, bundled_catalog, load_catalog
from gamepal.runtime.downloads import DownloadCancelled, DownloadError, download
from gamepal.runtime.llama import EngineError, LlamaRuntime, extract
from gamepal.runtime.service import EngineService
from gamepal.runtime.setup import apply_tier


def hw(
    os_name: str = "windows", arch: str = "x64", ram: float = 16, gpus: list[Gpu] | None = None, vulkan: bool = True
) -> HardwareInfo:
    return HardwareInfo(os_name, arch, "cpu", 8, ram, gpus or [], vulkan)  # type: ignore[arg-type]


# Equipo -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "tier"),
    [
        (hw("macos", "arm64", 8), 1),
        (hw("macos", "arm64", 16), 2),
        (hw("macos", "arm64", 36), 3),
        (hw(ram=6), 0),
        (hw(ram=32), 1),  # sin gráfica dedicada no pasa de 1
        (hw(ram=16, gpus=[Gpu("RTX 4060", "nvidia", 8.0)]), 2),
        (hw(ram=32, gpus=[Gpu("RTX 4090", "nvidia", 24.0)]), 3),
        (hw(ram=16, gpus=[Gpu("Intel UHD", "intel", 0.5, integrated=True)]), 1),
    ],
)
def test_propose_tier(info: HardwareInfo, tier: int) -> None:
    assert propose_tier(info).tier == tier


def test_parse_nvidia_smi() -> None:
    assert parse_nvidia_smi("NVIDIA GeForce RTX 4070, 12282\nbad line\n") == [
        Gpu("NVIDIA GeForce RTX 4070", "nvidia", 12.0)
    ]


# Catálogo ---------------------------------------------------------------------------------------


def test_bundled_catalog_is_consistent() -> None:
    catalog = bundled_catalog()
    assert {t.tier for t in catalog.tiers} == {0, 1, 2, 3}
    for preset in catalog.tiers:
        assert catalog.model(preset.reasoning) is not None
        if preset.vision:
            assert catalog.model(preset.vision).vision  # type: ignore[union-attr]
    for model in catalog.models:
        assert all(f.url.startswith("https://huggingface.co/") for f in model.files)
    for os_name, arch in (("windows", "x64"), ("windows", "arm64"), ("macos", "arm64"), ("linux", "x64")):
        assert catalog.runtime.choose(hw(os_name, arch)) is not None


@pytest.mark.parametrize(
    ("info", "variant"),
    [
        (hw("macos", "arm64"), "macos-arm64"),
        (hw(gpus=[Gpu("RTX", "nvidia", 8.0)]), "win-vulkan-x64"),
        (hw(gpus=[Gpu("RTX", "nvidia", 8.0)], vulkan=False), "win-cpu-x64"),
        (hw(gpus=[Gpu("UHD", "intel", 0.5, integrated=True)]), "win-cpu-x64"),
        (hw(arch="arm64", gpus=[Gpu("Adreno", "qualcomm", None, integrated=True)]), "win-cpu-arm64"),
        (hw("linux", gpus=[Gpu("AMD", "amd", 16.0)]), "linux-vulkan-x64"),
    ],
)
def test_runtime_variant_choice(info: HardwareInfo, variant: str) -> None:
    chosen = bundled_catalog().runtime.choose(info)
    assert chosen is not None
    assert chosen.id == variant


def test_catalog_rejects_bad_hashes(tmp_path: Path) -> None:
    text = (Path(__file__).parents[1] / "models" / "catalog.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "catalog.yaml"
    bad.write_text(text.replace("sha256: 37ae", "sha256: XXae", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="sha256"):
        load_catalog(bad)


def test_apply_tier() -> None:
    catalog = bundled_catalog()
    base = AppConfig.model_validate({"routing": {"reasoning": {"provider": "anthropic", "fallback": "local"}}})
    managed = apply_tier(base, catalog, 1, "managed")
    assert managed.provider.kind == "managed"
    assert managed.provider.model == catalog.tier(1).reasoning
    assert managed.provider.vision_model == catalog.tier(1).vision
    assert managed.routing.reasoning.provider == "local"
    assert managed.voice.stt.model == catalog.tier(1).stt
    assert managed.hardware_tier == 1
    cloud = apply_tier(base, catalog, 2, "cloud")
    assert cloud.routing.reasoning.provider == "anthropic"
    assert cloud.provider.kind == "local-openai"
    external = apply_tier(managed, catalog, 0, "external")
    assert external.provider.kind == "local-openai"
    assert external.provider.model == ""


# Descargas --------------------------------------------------------------------------------------

DATA = os.urandom(300_000)
SHA = hashlib.sha256(DATA).hexdigest()
URL = "https://huggingface.co/org/repo/resolve/abc/file.gguf"


def server(ranges: bool = True, body: bytes = DATA) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        start = 0
        header = request.headers.get("range")
        if header and ranges:
            start = int(header.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=body[start:])
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


def test_download_verifies_and_resumes(tmp_path: Path) -> None:
    dest = tmp_path / "m" / "file.gguf"
    dest.parent.mkdir()
    (dest.parent / "file.gguf.part").write_bytes(DATA[:100_000])
    seen: list[int] = []
    download(URL, dest, size=len(DATA), sha256=SHA, transport=server(), progress=lambda r, _t: seen.append(r))
    assert dest.read_bytes() == DATA
    assert seen[0] > 100_000  # continuó donde se quedó
    assert not (dest.parent / "file.gguf.part").exists()


def test_download_restarts_without_range_support(tmp_path: Path) -> None:
    dest = tmp_path / "file.gguf"
    (tmp_path / "file.gguf.part").write_bytes(b"x" * 1000)
    download(URL, dest, size=len(DATA), sha256=SHA, transport=server(ranges=False))
    assert dest.read_bytes() == DATA


def test_download_rejects_tampered_files(tmp_path: Path) -> None:
    dest = tmp_path / "file.gguf"
    tampered = bytes([DATA[0] ^ 1]) + DATA[1:]
    with pytest.raises(DownloadError, match="SHA-256"):
        download(URL, dest, size=len(DATA), sha256=SHA, transport=server(body=tampered))
    assert not dest.exists()
    assert not (tmp_path / "file.gguf.part").exists()
    with pytest.raises(DownloadError, match="más grande"):
        download(URL, dest, size=10, sha256=SHA, transport=server())


def test_download_only_from_allowed_hosts(tmp_path: Path) -> None:
    for url in ("http://huggingface.co/x", "https://evil.example/x"):
        with pytest.raises(DownloadError, match="no permitido"):
            download(url, tmp_path / "x", size=1, sha256=SHA, transport=server())


def test_download_can_be_cancelled(tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    with pytest.raises(DownloadCancelled):
        download(URL, tmp_path / "x", size=len(DATA), sha256=SHA, transport=server(), stop=stop)


# Descompresión segura ---------------------------------------------------------------------------


def test_extract_rejects_paths_outside(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../fuera.txt", "x")
    with pytest.raises(EngineError, match="no permitida"):
        extract(archive, tmp_path / "out")
    assert not (tmp_path / "fuera.txt").exists()

    def tar(name: str) -> Path:
        archive = tmp_path / f"{len(name)}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            info = tarfile.TarInfo(name)
            info.size = 1
            bundle.addfile(info, io.BytesIO(b"x"))
        return archive

    with pytest.raises(tarfile.TarError):
        extract(tar("../fuera.txt"), tmp_path / "out2")
    assert not (tmp_path / "fuera.txt").exists()
    # Las rutas absolutas se vuelven relativas: acaban dentro del destino.
    extract(tar("/tmp/dentro.txt"), tmp_path / "out3")
    assert (tmp_path / "out3" / "tmp" / "dentro.txt").is_file()


# Motor ------------------------------------------------------------------------------------------

FAKE_SERVER = """#!{python}
import http.server, os, sys
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
key = os.environ.get("LLAMA_API_KEY", "")
with open(os.path.join(os.path.dirname(__file__), "seen.txt"), "w") as out:
    out.write(" ".join(args) + "\\n" + ("key" if key else "nokey") + "\\n" + ",".join(sorted(os.environ)))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/health" or self.headers.get("authorization") == "Bearer " + key
        self.send_response(200 if ok else 401)
        self.end_headers()
        self.wfile.write(b'{{"data": [{{"id": "tiny"}}]}}')
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


def install_fake(runtime: LlamaRuntime, catalog: Catalog, info: HardwareInfo) -> Path:
    variant = catalog.runtime.choose(info)
    assert variant is not None
    folder = runtime.runtime_dir(variant) / "llama"
    folder.mkdir(parents=True)
    binary = folder / "llama-server"
    binary.write_text(FAKE_SERVER.format(python=sys.executable), encoding="utf-8")
    binary.chmod(0o755)
    (runtime.runtime_dir(variant) / "installed.json").write_text("{}", encoding="utf-8")
    return folder


def install_fake_model(runtime: LlamaRuntime, model_id: str) -> None:
    entry = runtime.catalog.model(model_id)
    assert entry is not None
    for index, item in enumerate(entry.files):
        path = runtime.model_path(entry, index)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.truncate(item.size)  # fichero disperso: ocupa el tamaño sin escribir gigas


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="el servidor falso es un script con shebang")


@posix_only
async def test_engine_service_starts_and_stops(tmp_path: Path) -> None:
    catalog = bundled_catalog()
    info = hw("linux", "x64", 16, vulkan=False) if sys.platform != "darwin" else hw("macos", "arm64", 16)
    config = AppConfig(provider=ProviderConfig(kind="managed", model="qwen3.5-0.8b"))
    changes: list[int] = []

    async def changed() -> None:
        changes.append(1)

    service = EngineService(
        EventBus(), tmp_path / "engine", tmp_path / "logs", catalog,
        config=lambda: config, on_change=changed, hardware=lambda: info,
    )  # fmt: skip
    await service.sync()
    assert service.endpoint is None  # sin motor instalado no arranca
    folder = install_fake(service.runtime, catalog, info)
    install_fake_model(service.runtime, "qwen3.5-0.8b")
    await service.sync(force=True)
    try:
        assert service.runtime.state == "running"
        url, key = service.endpoint  # type: ignore[misc]
        assert url.startswith("http://127.0.0.1:")
        args, key_flag, env = (folder / "seen.txt").read_text().splitlines()
        assert "--offline" in args
        assert "--models-preset" in args
        assert key not in args
        assert key_flag == "key"
        assert "ANTHROPIC_API_KEY" not in env.split(",")
        preset = (tmp_path / "engine" / "models.ini").read_text()
        assert "[qwen3.5-0.8b]" in preset
        assert "load-on-startup = true" in preset
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (await client.get(f"{url}/models")).status_code == 401
            assert (await client.get(f"{url}/models", headers={"authorization": f"Bearer {key}"})).status_code == 200
        status = await service.status()
        assert status["installed"]
        assert status["running_models"] == ["qwen3.5-0.8b"]
    finally:
        await service.stop()
    assert service.runtime.state == "stopped"
    assert changes


@posix_only
async def test_engine_reports_failures(tmp_path: Path) -> None:
    catalog = bundled_catalog()
    info = hw("macos", "arm64", 16) if sys.platform == "darwin" else hw("linux", "x64", 16, vulkan=False)
    runtime = LlamaRuntime(tmp_path, tmp_path / "logs", catalog)
    folder = install_fake(runtime, catalog, info)
    (folder / "llama-server").write_text("#!/bin/sh\necho 'modelo roto' >&2\nexit 3\n", encoding="utf-8")
    install_fake_model(runtime, "qwen3.5-0.8b")
    variant = catalog.runtime.choose(info)
    assert variant is not None
    with pytest.raises(EngineError, match="modelo roto"):
        await runtime.start(variant, [catalog.model("qwen3.5-0.8b")], context=4096, models_max=1, preload=None)  # type: ignore[list-item]
    assert runtime.state == "error"


@posix_only
def test_watchdog_kills_server_when_parent_dies() -> None:
    import subprocess
    import time

    from gamepal.runtime.llama import WATCHDOG

    parent = subprocess.Popen(["sleep", "30"])
    server = subprocess.Popen(["sleep", "60"], start_new_session=True)
    watchdog = subprocess.Popen(["/bin/sh", "-c", WATCHDOG, "vigia", str(parent.pid), str(server.pid)])
    try:
        time.sleep(0.5)
        assert server.poll() is None
        parent.kill()
        parent.wait()
        server.wait(timeout=8)  # el vigía cierra el grupo del servidor
    finally:
        for proc in (parent, server, watchdog):
            if proc.poll() is None:
                proc.kill()


async def test_install_checks_free_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    from collections import namedtuple

    async def changed() -> None:
        return None

    service = EngineService(
        EventBus(), tmp_path / "engine", tmp_path / "logs", bundled_catalog(),
        config=AppConfig, on_change=changed, hardware=lambda: hw("macos", "arm64", 16),
    )  # fmt: skip
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(10**12, 0, 10**9))
    with pytest.raises(EngineError, match="espacio"):
        await service.install(["gemma-4-12b"])
    assert not service.busy
