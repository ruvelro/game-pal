"""Detección del equipo y nivel de hardware recomendado.

Solo se leen datos del sistema (memoria, procesador, tarjetas gráficas); nada sale del equipo.
Cada consulta tiene su propio respaldo: si una falla, el nivel se calcula con lo que se sepa.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

GB = 1024**3
OsName = Literal["windows", "macos", "linux"]
Arch = Literal["x64", "arm64"]
COMMAND_TIMEOUT_S = 5


@dataclass(frozen=True, slots=True)
class Gpu:
    name: str
    vendor: str
    vram_gb: float | None = None
    # Memoria compartida con el sistema (Apple Silicon, gráficas integradas).
    integrated: bool = False


@dataclass(frozen=True, slots=True)
class HardwareInfo:
    os: OsName
    arch: Arch
    cpu: str
    cores: int
    ram_gb: float
    gpus: list[Gpu] = field(default_factory=list)
    vulkan: bool = False

    @property
    def unified(self) -> bool:
        return self.os == "macos" and self.arch == "arm64"

    def model_memory_gb(self) -> float:
        """Memoria que pueden ocupar los modelos con aceleración gráfica (0 si solo hay CPU)."""
        if self.unified:
            # macOS deja a Metal unas dos terceras partes de la memoria (tres cuartos en equipos grandes).
            return round(self.ram_gb * (0.75 if self.ram_gb > 36 else 0.66), 1)
        dedicated = [g.vram_gb or 0 for g in self.gpus if not g.integrated]
        return max(dedicated, default=0.0)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "unified": self.unified, "model_memory_gb": self.model_memory_gb()}


@dataclass(frozen=True, slots=True)
class TierProposal:
    tier: int
    reason: str


def propose_tier(info: HardwareInfo) -> TierProposal:
    """Nivel 0-3 de la spec. Sin GPU útil se queda en 1: los modelos grandes irían demasiado lentos."""
    ram, gpu = info.ram_gb, info.model_memory_gb()
    if gpu >= 16 and ram >= 30:
        return TierProposal(3, f"{gpu:g} GB para modelos y {ram:g} GB de RAM")
    if gpu >= 8 and ram >= 15:
        return TierProposal(2, f"{gpu:g} GB para modelos y {ram:g} GB de RAM")
    if ram >= 7.5:
        if gpu < 8 and ram >= 15:
            return TierProposal(1, "Sin gráfica con memoria suficiente: los modelos grandes irían lentos")
        return TierProposal(1, f"{ram:g} GB de RAM")
    return TierProposal(0, f"Solo {ram:g} GB de RAM: texto ligero y lectura por OCR")


# Detección --------------------------------------------------------------------------------------


def _run(args: list[str]) -> str:
    try:
        return subprocess.run(  # noqa: S603 - comandos fijos del sistema, sin shell
            args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def current_os() -> OsName:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def current_arch() -> Arch:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64", "armv8", "armv8l"}:
        return "arm64"
    if sys.platform == "win32" and os.environ.get("PROCESSOR_ARCHITEW6432", "").upper() == "ARM64":
        return "arm64"  # Python x64 emulado en Windows ARM64
    return "x64"


def _ram_bytes() -> int:
    if sys.platform == "darwin":
        out = _run(["/usr/sbin/sysctl", "-n", "hw.memsize"]).strip()
        return int(out) if out.isdigit() else 0
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
        kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
        return int(status.ullTotalPhys) if kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _cpu_name() -> str:
    if sys.platform == "darwin":
        return _run(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]).strip() or platform.processor()
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith(("model name", "hardware")):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    if sys.platform == "win32":
        try:
            import winreg

            key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
                return str(winreg.QueryValueEx(handle, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def parse_nvidia_smi(output: str) -> list[Gpu]:
    gpus = []
    for line in output.strip().splitlines():
        name, _, memory = line.rpartition(",")
        if name and memory.strip().isdigit():
            gpus.append(Gpu(name.strip(), "nvidia", round(int(memory) / 1024, 1)))
    return gpus


def _nvidia() -> list[Gpu]:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return []
    return parse_nvidia_smi(_run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]))


def _vendor(name: str) -> str:
    lowered = name.lower()
    for vendor, keys in (
        ("nvidia", ("nvidia", "geforce", "rtx", "quadro")),
        ("amd", ("amd", "radeon")),
        ("intel", ("intel",)),
        ("qualcomm", ("qualcomm", "adreno", "snapdragon")),
        ("apple", ("apple",)),
    ):
        if any(k in lowered for k in keys):
            return vendor
    return "other"


def _integrated(vendor: str, name: str, vram_gb: float | None) -> bool:
    lowered = name.lower()
    if vendor in {"qualcomm", "apple"}:
        return True
    if vendor == "intel":
        # Las Arc dedicadas llevan modelo (A770, B580); «Arc Graphics» a secas es la integrada.
        return re.search(r"arc(\(tm\))?\s+[ab]\d{3}", lowered) is None
    if vendor == "amd" and re.search(r"radeon\(tm\) graphics|vega \d+ graphics|\d{3}m\b", lowered):
        return True
    return vram_gb is not None and vram_gb < 2


def _windows_gpus() -> list[Gpu]:
    """Nombre y memoria de cada gráfica desde el registro (la cifra de WMI se queda en 4 GB)."""
    if sys.platform != "win32":
        return []
    import winreg

    gpus: list[Gpu] = []
    root = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as handle:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(handle, index)
                except OSError:
                    break
                index += 1
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(handle, sub) as device:
                        name = str(winreg.QueryValueEx(device, "DriverDesc")[0])
                        try:
                            memory = int(winreg.QueryValueEx(device, "HardwareInformation.qwMemorySize")[0])
                        except (OSError, ValueError, TypeError):
                            memory = 0
                except OSError:
                    continue
                if "basic" in name.lower() or "remote" in name.lower():
                    continue
                vendor = _vendor(name)
                vram = round(memory / GB, 1) if memory else None
                gpus.append(Gpu(name, vendor, vram, _integrated(vendor, name, vram)))
    except OSError:
        log.debug("No se pudo leer el registro de gráficas", exc_info=True)
    return gpus


def _linux_gpus() -> list[Gpu]:
    gpus: list[Gpu] = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        device = card / "device"
        try:
            vendor_id = (device / "vendor").read_text().strip()
        except OSError:
            continue
        vendor = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}.get(vendor_id, "other")
        if vendor == "nvidia":
            continue  # lo informa nvidia-smi con su memoria real
        vram = None
        with contextlib.suppress(OSError, ValueError):
            vram = round(int((device / "mem_info_vram_total").read_text()) / GB, 1)
        name = f"{vendor.upper()} GPU" if vendor != "other" else "GPU"
        integrated = vendor == "intel" or (vram is not None and vram < 2)
        gpus.append(Gpu(name, vendor, vram, integrated))
    return gpus


def _mac_gpus() -> list[Gpu]:
    out = _run(["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"])
    try:
        items = json.loads(out).get("SPDisplaysDataType", []) if out else []
    except ValueError:
        items = []
    gpus = []
    for item in items:
        name = str(item.get("sppci_model") or item.get("_name") or "GPU")
        vendor = _vendor(name)
        gpus.append(Gpu(name, vendor, None, integrated=vendor in {"apple", "intel"}))
    return gpus


def _has_vulkan(os_name: OsName) -> bool:
    if os_name == "windows":
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return Path(system_root, "System32", "vulkan-1.dll").is_file()
    if os_name == "linux":
        import ctypes.util

        return ctypes.util.find_library("vulkan") is not None
    return False


def detect() -> HardwareInfo:
    os_name = current_os()
    gpus: list[Gpu] = []
    try:
        if os_name == "macos":
            gpus = _mac_gpus()
        else:
            gpus = _nvidia()
            others = _windows_gpus() if os_name == "windows" else _linux_gpus()
            gpus += [g for g in others if g.vendor != "nvidia" or not gpus]
    except Exception:
        log.warning("No se pudieron detectar las gráficas", exc_info=True)
    return HardwareInfo(
        os=os_name,
        arch=current_arch(),
        cpu=_cpu_name(),
        cores=os.cpu_count() or 1,
        ram_gb=round(_ram_bytes() / GB, 1),
        gpus=gpus,
        vulkan=_has_vulkan(os_name),
    )
