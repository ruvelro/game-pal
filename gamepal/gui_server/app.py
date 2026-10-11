"""Servidor de la GUI: API REST + WebSocket de eventos, solo en loopback y con token.

El token se entrega a la GUI en el fragmento de la URL (#token=…), que el navegador nunca
envía al servidor, y la GUI lo usa en la cabecera Authorization y al abrir el WebSocket.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from PIL import Image
from pydantic import BaseModel, Field, ValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from gamepal import __version__
from gamepal.autodetect import pick_model
from gamepal.checks import blocking, daemon_checks, discover_servers
from gamepal.config import AppConfig
from gamepal.daemon import Daemon
from gamepal.events import AnswerUpdate, SpeechStopped
from gamepal.keystore import CLOUDS, SecretStoreError
from gamepal.knowledge import KnowledgeError
from gamepal.knowledge.service import MAX_FILE_BYTES as MAX_UPLOAD_BYTES
from gamepal.perception.classifier import classify
from gamepal.perception.vocabulary import expand_path
from gamepal.profiles.lint import lint
from gamepal.providers import Message, ProviderError
from gamepal.security import UnsafeUrlError, allowed_host_header, tokens_match
from gamepal.system import can_open_settings, open_settings, total_memory_gb
from gamepal.ui_build import STATIC_DIR
from gamepal.voice.audio import list_devices
from gamepal.voice.models import PIPER_DEFAULT_VOICES

log = logging.getLogger(__name__)

WS_AUTH_TIMEOUT_S = 5.0


class SecurityHeaders(BaseHTTPMiddleware):
    def __init__(self, app: Any, port: int) -> None:
        super().__init__(app)
        self.port = port

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not allowed_host_header(request.headers.get("host"), self.port):
            return JSONResponse({"detail": "Host no permitido"}, status_code=status.HTTP_421_MISDIRECTED_REQUEST)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
            f"connect-src 'self' ws://127.0.0.1:{self.port} ws://localhost:{self.port}; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PttRequest(BaseModel):
    down: bool


class SourceRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    depth: int = Field(default=1, ge=0, le=5)
    max_pages: int | None = Field(default=None, ge=1, le=2000)
    include: list[str] = Field(default_factory=list, max_length=20)
    exclude: list[str] = Field(default_factory=list, max_length=20)


class RefreshRequest(BaseModel):
    source_id: int | None = None


class LessonRequest(BaseModel):
    text: str = Field(min_length=2, max_length=600)


class SecretRequest(BaseModel):
    key: str = Field(min_length=8, max_length=400, pattern=r"^[A-Za-z0-9_\-.:]+$")


class MuteRequest(BaseModel):
    muted: bool


class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    include_screen: bool = False


def create_app(daemon: Daemon, token: str, port: int) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # El daemon vive en el mismo bucle de eventos que el servidor.
        daemon.attach_gui(port, token)
        await daemon.start()
        try:
            yield
        finally:
            await daemon.stop()

    app = FastAPI(
        title="game-pal", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.add_middleware(SecurityHeaders, port=port)

    def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        given = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else None
        if not tokens_match(token, given):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token no válido")

    auth = [Depends(require_token)]

    @app.get("/api/status", dependencies=auth)
    async def get_status() -> dict[str, Any]:
        c = daemon.c
        profile = c.profile.profile if c.profile else None
        return {
            "version": __version__,
            "paused": c.watcher.paused if c.watcher else True,
            "profile": {"id": profile.id, "name": profile.name} if profile else None,
            "capture_backend": c.backend.name if c.backend else None,
            "ocr_backend": c.ocr.name if c.ocr else None,
            "errors": c.errors,
            "events": [e.to_message() for e in daemon.bus.last_events()],
        }

    @app.get("/api/setup", dependencies=auth)
    async def setup() -> dict[str, Any]:
        """Qué funciona, qué falta y cómo arreglarlo. `ready` es falso si algo impide aconsejar."""
        checks = await daemon_checks(daemon)
        return {"ready": not blocking(checks), "checks": [c.model_dump() for c in checks], "notes": daemon.notes}

    @app.post("/api/setup/settings/{pane}", dependencies=auth)
    async def setup_open_settings(pane: str) -> dict[str, bool]:
        """Abre el panel del sistema donde se concede un permiso (Grabación de pantalla, Micrófono…)."""
        if not can_open_settings(pane):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Este sistema no tiene ese panel")
        return {"opened": await asyncio.to_thread(open_settings, pane)}

    @app.post("/api/setup/autodetect", dependencies=auth)
    async def setup_autodetect() -> dict[str, list[str]]:
        """Busca ya el juego abierto y, si falta, un modelo local. Devuelve lo que ha cambiado."""
        return {"changes": await daemon.autodetect()}

    @app.get("/api/config", dependencies=auth)
    async def get_config() -> dict[str, Any]:
        return daemon.config.model_dump(mode="json")

    @app.put("/api/config", dependencies=auth)
    async def put_config(request: Request) -> dict[str, Any]:
        try:
            config = AppConfig.model_validate(await request.json())
            # Valida la URL del proveedor antes de guardar nada.
            from gamepal.providers import create_provider

            probe = create_provider(config.provider)
            await probe.aclose()
        except (ValidationError, UnsafeUrlError, ProviderError, json.JSONDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        for profile_id, folder in config.game_dirs.items():
            if folder and not expand_path(folder).is_dir():
                raise HTTPException(422, f"La carpeta del juego para '{profile_id}' no existe: {folder}")
        await daemon.apply(config)
        return daemon.config.model_dump(mode="json")

    @app.get("/api/profiles", dependencies=auth)
    async def list_profiles() -> dict[str, Any]:
        profiles, errors = await asyncio.to_thread(daemon.profiles.discover)
        return {
            "profiles": [
                {
                    "id": p.profile.id,
                    "name": p.profile.name,
                    "description": p.profile.description,
                    "bundled": p.bundled,
                    "screens": len(p.profile.screens),
                    "sources": len(p.profile.knowledge.sources),
                }
                for p in profiles.values()
            ],
            "errors": errors,
        }

    @app.get("/api/profile", dependencies=auth)
    async def profile_info() -> dict[str, Any]:
        loaded = daemon.c.profile
        if loaded is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, daemon.c.errors.get("profile", "Sin perfil"))
        warnings = await asyncio.to_thread(lint, loaded)
        vocabulary = daemon.c.vocabulary
        return {
            "id": loaded.profile.id,
            "name": loaded.profile.name,
            "screens": [{"id": s.id, "description": s.description} for s in loaded.profile.screens],
            "uses_game_text": bool(loaded.profile.vocabulary.game_text),
            "install_dir": str(daemon.c.install_dir) if daemon.c.install_dir else None,
            "vocabulary": vocabulary.sets(),
            "sources": [{"set": v.set, "origin": v.origin, "entries": v.entries} for v in vocabulary.sources],
            "warnings": warnings,
        }

    @app.post("/api/profile/test", dependencies=auth)
    async def profile_test() -> dict[str, Any]:
        """Evalúa todas las pantallas del perfil sobre la última lectura, para depurar perfiles."""
        loaded, read = daemon.c.profile, daemon.last_read
        if loaded is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Sin perfil")
        if read is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Aún no hay ninguna lectura de pantalla")
        watcher = daemon.c.watcher
        frame = watcher.frame_for_read(read.frame_id) if watcher else None
        result = await asyncio.to_thread(
            classify,
            loaded.profile,
            daemon.c.vocabulary,
            read.lines,
            daemon.config.output_language,
            frame.image if frame else None,
        )
        return {
            "screen": result.screen_id,
            "confidence": round(result.confidence, 3),
            "certain": result.certain,
            "complete": result.complete,
            "data": result.data,
            "lines": len(read.lines),
            "candidates": [
                {
                    "id": c.screen.id,
                    "description": c.screen.description,
                    "matched": c.matched,
                    "score": round(c.score, 3),
                    "evidence": c.evidence[:6],
                    "failed": c.failed,
                }
                for c in result.candidates
            ],
        }

    @app.get("/api/windows", dependencies=auth)
    async def list_windows() -> list[dict[str, Any]]:
        backend = daemon.c.backend
        if backend is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, daemon.c.errors.get("capture", "Sin captura"))
        windows = await asyncio.to_thread(backend.list_windows)
        return [
            {"title": w.title, "process": w.process, "width": w.width, "height": w.height}
            for w in windows
            if w.width >= 200 and w.height >= 150 and w.title
        ]

    @app.post("/api/watcher/pause", dependencies=auth)
    async def pause() -> dict[str, bool]:
        daemon.pause()
        return {"paused": True}

    @app.post("/api/watcher/resume", dependencies=auth)
    async def resume() -> dict[str, bool]:
        daemon.resume()
        return {"paused": False}

    @app.get("/api/frame.jpg", dependencies=auth)
    async def frame() -> Response:
        watcher = daemon.c.watcher
        current = watcher.latest_frame if watcher else None
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Aún no hay captura")

        def encode() -> bytes:
            picture = Image.fromarray(current.image, "RGB")
            picture.thumbnail((960, 960))
            buffer = io.BytesIO()
            picture.save(buffer, "JPEG", quality=80)
            return buffer.getvalue()

        return Response(await asyncio.to_thread(encode), media_type="image/jpeg")

    @app.get("/api/text", dependencies=auth)
    async def last_text() -> dict[str, Any]:
        read = daemon.last_read
        return read.model_dump(mode="json") if read else {"lines": []}

    @app.get("/api/provider/models", dependencies=auth)
    async def provider_models() -> dict[str, Any]:
        provider = daemon.c.provider
        if provider is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, daemon.c.errors.get("provider", "Sin proveedor"))
        try:
            caps = await provider.capabilities()
        except ProviderError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return {"models": list(caps.models), "local": caps.local}

    @app.post("/api/provider/ask", dependencies=auth)
    async def ask(body: AskRequest) -> dict[str, Any]:
        provider = daemon.c.provider
        if provider is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, daemon.c.errors.get("provider", "Sin proveedor"))
        system = (
            daemon.c.profile.read_prompt("advisor") if daemon.c.profile else None
        ) or "Eres un compañero de juego útil y conciso."
        system = system.replace("{output_language}", daemon.config.output_language)
        context = ""
        if body.include_screen and daemon.current_screen is not None:
            screen = daemon.current_screen
            details = json.dumps(screen.data, ensure_ascii=False)
            context += (
                f'\n\n<pantalla_reconocida id="{screen.screen_id}">\n{screen.name}\n{details}\n</pantalla_reconocida>'
            )
        if body.include_screen and daemon.last_read is not None:
            text = "\n".join(line.text for line in daemon.last_read.lines)
            context += f"\n\n<texto_en_pantalla>\n{text}\n</texto_en_pantalla>"
        messages = [Message("system", system), Message("user", body.prompt + context)]
        try:
            result = await provider.chat(messages, max_tokens=600)
        except ProviderError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return {
            "text": result.text,
            "model": result.model,
            "elapsed_ms": round(result.elapsed_ms),
            "usage": {"input": result.usage.input_tokens, "output": result.usage.output_tokens},
        }

    # Conocimiento ---------------------------------------------------------------------------

    def _knowledge_store() -> Any:
        store = daemon.knowledge.store
        if store is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No hay ningún perfil activo")
        return store

    @app.get("/api/knowledge", dependencies=auth)
    async def knowledge_info() -> dict[str, Any]:
        store = _knowledge_store()
        config = daemon.config.knowledge
        return {
            "sources": [
                {
                    "id": s.id, "kind": s.kind, "location": s.location, "origin": s.origin, "options": s.options,
                    "status": s.status, "error": s.error, "updated_at": s.updated_at, "pages": s.pages,
                    "chunks": s.chunks,
                }
                for s in await asyncio.to_thread(store.sources)
            ],
            "stats": await asyncio.to_thread(store.stats),
            "embeddings": config.embeddings,
            "embedder_error": daemon.knowledge.embedder_error,
            "refresh": daemon.c.profile.profile.knowledge.refresh if daemon.c.profile else "manual",
            "current": daemon.knowledge.current,
        }  # fmt: skip

    @app.post("/api/knowledge/sources", dependencies=auth)
    async def knowledge_add(body: SourceRequest) -> dict[str, Any]:
        try:
            source = daemon.knowledge.add_url(body.url, body.depth, body.max_pages, body.include, body.exclude)
        except KnowledgeError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": source.id}

    @app.post("/api/knowledge/upload", dependencies=auth)
    async def knowledge_upload(files: list[UploadFile]) -> dict[str, Any]:
        added = []
        for upload in files[:20]:
            data = await upload.read(MAX_UPLOAD_BYTES + 1)
            try:
                source = daemon.knowledge.add_file(upload.filename or "fichero", data)
            except KnowledgeError as exc:
                raise HTTPException(422, f"{upload.filename}: {exc}") from exc
            added.append(source.id)
        return {"ids": added}

    @app.delete("/api/knowledge/sources/{source_id}", dependencies=auth)
    async def knowledge_remove(source_id: int) -> dict[str, bool]:
        try:
            daemon.knowledge.remove(source_id)
        except KnowledgeError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"removed": True}

    @app.post("/api/knowledge/refresh", dependencies=auth)
    async def knowledge_refresh(body: RefreshRequest) -> dict[str, int]:
        try:
            count = daemon.knowledge.refresh(body.source_id)
        except KnowledgeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"queued": count}

    @app.get("/api/knowledge/search", dependencies=auth)
    async def knowledge_search(q: str) -> list[dict[str, Any]]:
        hits = await daemon.knowledge.search(q[:500])
        return [
            {"title": h.title, "heading": h.heading, "text": h.text[:600], "url": h.url, "score": round(h.score, 4)}
            for h in hits
        ]

    # Proveedores, claves y actividad ---------------------------------------------------------

    @app.get("/api/providers", dependencies=auth)
    async def providers_info() -> dict[str, Any]:
        configured = await asyncio.to_thread(daemon.secrets.configured)
        return {
            "keys": configured,
            "routing": daemon.config.routing.model_dump(),
            "summary": daemon.routing_summary(),
            "vision_enabled": daemon.vision_enabled(),
        }

    @app.put("/api/providers/{name}/key", dependencies=auth)
    async def provider_set_key(name: str, body: SecretRequest) -> dict[str, bool]:
        if name not in CLOUDS:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Proveedor desconocido")
        try:
            await asyncio.to_thread(daemon.secrets.set, name, body.key)  # type: ignore[arg-type]
        except SecretStoreError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        await daemon.refresh_providers()
        return {"configured": True}

    @app.delete("/api/providers/{name}/key", dependencies=auth)
    async def provider_delete_key(name: str) -> dict[str, bool]:
        if name not in CLOUDS:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Proveedor desconocido")
        await asyncio.to_thread(daemon.secrets.delete, name)  # type: ignore[arg-type]
        await daemon.refresh_providers()
        return {"configured": False}

    @app.get("/api/providers/local/discover", dependencies=auth)
    async def provider_discover() -> dict[str, Any]:
        """Servidores de modelos en marcha en este equipo, con el modelo que game-pal recomienda en cada uno."""
        found = await discover_servers(daemon.probe, include=daemon.config.provider.base_url)
        memory = total_memory_gb()
        servers = [
            {"name": name, "url": url, "models": list(models), "recommended": pick_model(models, memory)}
            for name, url, models in found
        ]
        return {"servers": servers}

    @app.get("/api/providers/{name}/models", dependencies=auth)
    async def provider_list_models(name: str) -> dict[str, Any]:
        if name not in ("local", *CLOUDS):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Proveedor desconocido")
        try:
            caps = await daemon.hub.client(name).capabilities()
        except ProviderError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return {"models": list(caps.models)}

    @app.get("/api/activity", dependencies=auth)
    async def activity(hours: int = 24) -> dict[str, Any]:
        since = time.time() - max(1, min(hours, 24 * 30)) * 3600
        summary = await asyncio.to_thread(daemon.activity.summary, since)
        summary["recent"] = await asyncio.to_thread(daemon.activity.recent, 40)
        summary["hours"] = hours
        return summary

    # Evidencia y memoria -----------------------------------------------------------------------

    @app.get("/api/evidence", dependencies=auth)
    async def evidence() -> dict[str, Any]:
        service = daemon.evidence
        snapshot = service.ledger.snapshot()
        untrusted = service.untrusted
        snapshot["save"].update(
            {
                "folder": str(service.folder) if service.folder else None,
                "error": service.error,
                "reader": service.reader is not None,
                "declared": bool(service.profile and service.profile.profile.evidence.save_reader),
                "untrusted": {"profile": untrusted.profile_id, "digest": untrusted.digest} if untrusted else None,
            }
        )
        return snapshot

    @app.post("/api/evidence/reload", dependencies=auth)
    async def evidence_reload() -> dict[str, bool]:
        reader = daemon.evidence.reader
        if reader is None:
            raise HTTPException(status.HTTP_409_CONFLICT, daemon.evidence.error or "Este perfil no lee guardados")
        daemon.evidence.forget_save()
        return {"read": await daemon.evidence.check_save(reader)}

    @app.post("/api/profile/trust", dependencies=auth)
    async def profile_trust() -> dict[str, bool]:
        untrusted = daemon.evidence.untrusted
        if untrusted is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No hay ningún plugin pendiente de confianza")
        trusted = {**daemon.config.trusted_plugins, untrusted.profile_id: untrusted.digest}
        await daemon.apply(daemon.config.model_copy(update={"trusted_plugins": trusted}))
        return {"trusted": True}

    def _profile_id() -> str:
        if daemon.c.profile is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No hay ningún perfil activo")
        return daemon.c.profile.profile.id

    @app.get("/api/memory", dependencies=auth)
    async def memory_info() -> dict[str, Any]:
        profile = _profile_id()
        sessions, lessons = await asyncio.to_thread(
            daemon.memory.use, lambda store: (store.sessions(profile), store.lessons(profile))
        )
        return {
            "enabled": daemon.config.memory.enabled,
            "current": daemon.memory.session,
            "sessions": sessions,
            "lessons": lessons,
        }

    @app.get("/api/memory/sessions/{session_id}", dependencies=auth)
    async def memory_session(session_id: int) -> list[dict[str, Any]]:
        turns: list[dict[str, Any]] = await asyncio.to_thread(daemon.memory.use, lambda s: s.turns(session_id))
        return turns

    @app.delete("/api/memory/sessions/{session_id}", dependencies=auth)
    async def memory_delete_session(session_id: int) -> dict[str, bool]:
        await asyncio.to_thread(daemon.memory.use, lambda s: s.delete_session(session_id))
        return {"deleted": True}

    @app.post("/api/memory/lessons", dependencies=auth)
    async def memory_add_lesson(body: LessonRequest) -> dict[str, int]:
        profile = _profile_id()
        lesson_id: int = await asyncio.to_thread(daemon.memory.use, lambda s: s.add_lesson(profile, body.text))
        return {"id": lesson_id}

    @app.delete("/api/memory/lessons/{lesson_id}", dependencies=auth)
    async def memory_delete_lesson(lesson_id: int) -> dict[str, bool]:
        await asyncio.to_thread(daemon.memory.use, lambda s: s.delete_lesson(lesson_id))
        return {"deleted": True}

    @app.delete("/api/memory", dependencies=auth)
    async def memory_wipe() -> dict[str, bool]:
        profile = _profile_id()
        await asyncio.to_thread(daemon.memory.use, lambda s: s.wipe(profile))
        return {"deleted": True}

    @app.get("/api/memory/export", dependencies=auth)
    async def memory_export() -> Response:
        _profile_id()
        body = await asyncio.to_thread(daemon.memory.dumps)
        return Response(
            body,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="game-pal-memoria.json"'},
        )

    # Asistente y voz --------------------------------------------------------------------------

    @app.post("/api/assistant/ask", dependencies=auth)
    async def assistant_ask(body: TextRequest) -> dict[str, str]:
        return {"id": daemon.assistant.ask(body.text)}

    @app.post("/api/assistant/stop", dependencies=auth)
    async def assistant_stop() -> dict[str, bool]:
        daemon.assistant.cancel()
        daemon.bus.publish(SpeechStopped(reason="gui"))
        return {"stopped": True}

    @app.post("/api/proactive", dependencies=auth)
    async def proactive(body: MuteRequest) -> dict[str, bool]:
        daemon.assistant.proactive_muted = body.muted
        return {"muted": daemon.assistant.proactive_muted}

    @app.post("/api/voice/ptt", dependencies=auth)
    async def voice_ptt(body: PttRequest) -> dict[str, bool]:
        controller = daemon.voice.controller
        if controller is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "La entrada de voz está desactivada")
        if body.down:
            controller.ptt_press()
        else:
            controller.ptt_release()
        return {"down": body.down}

    @app.post("/api/voice/say", dependencies=auth)
    async def voice_say(body: TextRequest) -> dict[str, bool]:
        speech = daemon.voice.speech
        if speech is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "La voz de salida está desactivada")
        speech.say(body.text)
        return {"ok": True}

    @app.get("/api/voice/devices", dependencies=auth)
    async def voice_devices() -> dict[str, Any]:
        devices: dict[str, Any]
        try:
            devices = dict(await asyncio.to_thread(list_devices))
        except Exception as exc:
            devices = {"inputs": [], "outputs": [], "error": str(exc)}
        gamepad = daemon.voice.gamepad
        return {
            **devices,
            "gamepads": gamepad.connected if gamepad else [],
            "last_button": gamepad.last_button if gamepad else None,
            "gamepad_error": gamepad.error if gamepad else None,
            "voices": PIPER_DEFAULT_VOICES,
            "proactive_muted": daemon.assistant.proactive_muted,
        }

    @app.post("/api/overlay/test", dependencies=auth)
    async def overlay_test() -> dict[str, bool]:
        sample = "Así se verán los consejos sobre el juego. Puedes cambiar la esquina, el tamaño y la opacidad."
        daemon.bus.publish(
            AnswerUpdate(
                id="overlay-test", kind="proactive", title="game-pal", text=sample, done=True, outputs=["overlay"]
            )
        )
        return {"ok": True}

    @app.websocket("/api/events")
    async def events(socket: WebSocket) -> None:
        if not allowed_host_header(socket.headers.get("host"), port):
            await socket.close(code=1008)
            return
        origin = socket.headers.get("origin")
        if origin is not None and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            await socket.close(code=1008)
            return
        await socket.accept()
        try:
            hello = await asyncio.wait_for(socket.receive_json(), WS_AUTH_TIMEOUT_S)
        except (TimeoutError, WebSocketDisconnect, ValueError):
            await socket.close(code=1008)
            return
        if not isinstance(hello, dict) or not tokens_match(token, hello.get("token")):
            await socket.close(code=1008)
            return
        async with daemon.bus.subscribe(maxsize=128) as sub:
            for event in daemon.bus.last_events():
                await socket.send_json(event.to_message())
            receiver = asyncio.create_task(_drain(socket))
            try:
                while not receiver.done():
                    getter = asyncio.create_task(sub.get())
                    done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                    if getter in done:
                        await socket.send_json(getter.result().to_message())
                    else:
                        getter.cancel()
            except WebSocketDisconnect:
                pass
            finally:
                receiver.cancel()

    @app.get("/{path:path}", include_in_schema=False)
    async def static(path: str) -> Response:
        if not STATIC_DIR.is_dir():
            return HTMLResponse(_MISSING_UI, status_code=200)
        target = (STATIC_DIR / path).resolve()
        if path and target.is_file() and target.is_relative_to(STATIC_DIR):
            return FileResponse(target)
        return FileResponse(STATIC_DIR / "index.html")

    return app


async def _drain(socket: WebSocket) -> None:
    """Consume mensajes entrantes para detectar el cierre del cliente."""
    while True:
        await socket.receive_text()


_MISSING_UI = """<!doctype html><meta charset="utf-8"><title>game-pal</title>
<body style="font-family:system-ui;background:#0b1d21;color:#e6f2ee;padding:40px;max-width:640px;line-height:1.5">
<h1>Falta compilar la interfaz</h1>
<p>game-pal la compila solo al arrancar, pero necesita <a style="color:#7fd1b9" href="https://nodejs.org">Node.js 22
o superior</a>. Instálalo, cierra game-pal y vuelve a arrancarlo con <code>uv run game-pal</code>.</p>
<p>Si ya tienes Node.js, mira en la terminal el motivo del fallo, o ejecuta <code>uv run game-pal doctor</code>.</p>
</body>"""
