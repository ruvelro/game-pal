"""Permisos del sistema que necesita game-pal, y pedirlos al arrancar para no tener que buscarlos a mano.

Solo macOS los pide con un diálogo:
- Grabación de pantalla, para ver la ventana del juego.
- Monitorización de entrada, para los atajos de teclado globales (hablar, chat, silenciar avisos).
- Micrófono, para la voz.

Cada diálogo sale una sola vez: si el jugador lo rechaza, el sistema ya no deja volver a preguntar y la GUI
ofrece abrir el panel de Ajustes donde se activa. En Windows solo el micrófono tiene permiso y no se puede
pedir: se comprueba y, si está bloqueado, se abre su panel. Linux no tiene permisos de este tipo.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from gamepal.config import AppConfig

log = logging.getLogger(__name__)

Need = Literal["screen", "input", "microphone"]
# undetermined: aún no se ha preguntado (se puede pedir). unknown: no aplica o no se sabe.
State = Literal["granted", "denied", "undetermined", "unknown"]

_IOKIT = "/System/Library/Frameworks/IOKit.framework/IOKit"
_APP_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_AVFOUNDATION = "/System/Library/Frameworks/AVFoundation.framework"
# IOHIDRequestType y IOHIDAccessType de IOKit/hid/IOHIDLib.h.
_LISTEN_EVENT = 1
_HID_ACCESS: dict[int, State] = {0: "granted", 1: "denied", 2: "undetermined"}
# AVAuthorizationStatus: notDetermined, restricted, denied, authorized.
_AV_STATUS: dict[int, State] = {0: "undetermined", 1: "denied", 2: "denied", 3: "granted"}
_AUDIO = "soun"  # AVMediaTypeAudio
_MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def needed(config: AppConfig) -> set[Need]:
    """Lo que necesita esta configuración. Ver la pantalla siempre; lo demás, según lo activado."""
    needs: set[Need] = {"screen"}
    voice, output = config.voice, config.output
    keys = [
        voice.enabled and voice.push_to_talk.enabled and voice.push_to_talk.key,
        output.proactive.enabled and output.proactive.mute_key,
        output.overlay.enabled and output.overlay.chat_key,
    ]
    if any(keys):
        needs.add("input")
    if voice.enabled:
        needs.add("microphone")
    return needs


# macOS ---------------------------------------------------------------------------------------


def _screen_state() -> State:
    try:
        import Quartz

        return "granted" if Quartz.CGPreflightScreenCaptureAccess() else "undetermined"
    except Exception:
        return "unknown"


def _accessibility_trusted() -> bool:
    try:
        lib = ctypes.cdll.LoadLibrary(_APP_SERVICES)
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except (OSError, AttributeError):
        return False


def _iokit() -> ctypes.CDLL:
    lib = ctypes.cdll.LoadLibrary(_IOKIT)
    lib.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
    lib.IOHIDCheckAccess.restype = ctypes.c_uint32
    lib.IOHIDRequestAccess.argtypes = [ctypes.c_uint32]
    lib.IOHIDRequestAccess.restype = ctypes.c_bool
    return lib


def _input_state() -> State:
    # Accesibilidad también deja escuchar el teclado: con cualquiera de los dos basta.
    if _accessibility_trusted():
        return "granted"
    try:
        return _HID_ACCESS.get(int(_iokit().IOHIDCheckAccess(_LISTEN_EVENT)), "unknown")
    except (OSError, AttributeError):
        return "unknown"


def _capture_device() -> object:
    import objc

    objc.loadBundle("AVFoundation", {}, bundle_path=_AVFOUNDATION)
    return objc.lookUpClass("AVCaptureDevice")


def _microphone_state_mac() -> State:
    try:
        device = _capture_device()
        return _AV_STATUS.get(int(device.authorizationStatusForMediaType_(_AUDIO)), "unknown")  # type: ignore[attr-defined]
    except Exception:
        return "unknown"


def _request_microphone_mac() -> None:
    import objc

    device = _capture_device()
    # Sin el envoltorio de AVFoundation, PyObjC no conoce la firma del bloque: se la decimos.
    handler = {"retval": {"type": b"v"}, "arguments": {0: {"type": b"^v"}, 1: {"type": b"Z"}}}
    objc.registerMetaDataForSelector(
        b"AVCaptureDevice",
        b"requestAccessForMediaType:completionHandler:",
        {"arguments": {3: {"callable": handler}}},
    )
    device.requestAccessForMediaType_completionHandler_(  # type: ignore[attr-defined]
        _AUDIO, lambda granted: log.info("Micrófono %s", "concedido" if granted else "denegado")
    )


# Windows -------------------------------------------------------------------------------------


def _microphone_state_windows() -> State:
    if sys.platform != "win32":
        return "unknown"
    import winreg

    values: list[str] = []
    for root, path in (
        (winreg.HKEY_LOCAL_MACHINE, _MIC_KEY),
        (winreg.HKEY_CURRENT_USER, _MIC_KEY),
        (winreg.HKEY_CURRENT_USER, _MIC_KEY + r"\NonPackaged"),
    ):
        try:
            with winreg.OpenKey(root, path) as key:
                values.append(str(winreg.QueryValueEx(key, "Value")[0]))
        except OSError:
            continue
    if "Deny" in values:
        return "denied"
    return "granted" if values else "unknown"


# Común ---------------------------------------------------------------------------------------


def state(need: Need) -> State:
    if sys.platform == "darwin":
        if need == "screen":
            return _screen_state()
        if need == "input":
            return _input_state()
        return _microphone_state_mac()
    if sys.platform == "win32" and need == "microphone":
        try:
            return _microphone_state_windows()
        except Exception:
            return "unknown"
    return "unknown"


def request(needs: set[Need]) -> dict[Need, State]:
    """Pide al sistema lo que falte. Solo sale el diálogo de lo que aún no se ha preguntado nunca."""
    states: dict[Need, State] = {}
    for need in sorted(needs):
        current = state(need)
        if sys.platform == "darwin" and current == "undetermined":
            try:
                if need == "screen":
                    import Quartz

                    Quartz.CGRequestScreenCaptureAccess()
                elif need == "input":
                    _iokit().IOHIDRequestAccess(_LISTEN_EVENT)
                else:
                    _request_microphone_mac()
                log.info("Pedido el permiso del sistema: %s", need)
            except Exception as exc:
                log.warning("No se pudo pedir el permiso %s: %s", need, exc)
        states[need] = current
    return states
