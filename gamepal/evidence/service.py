"""Alimenta el registro de evidencia: guardado del juego, pantallas y declaraciones del jugador."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gamepal.bus import EventBus
from gamepal.config import AppConfig
from gamepal.events import EvidenceChanged, QuestionAsked, ScreenDetected, StatusChanged
from gamepal.evidence.ledger import Fact, Ledger, Rank
from gamepal.perception.text import normalize
from gamepal.perception.vocabulary import Vocabulary, expand_path
from gamepal.plugins import PluginError, SaveReader, SaveSnapshot, UntrustedPluginError, save_reader
from gamepal.profiles import LoadedProfile

log = logging.getLogger(__name__)

POLL_S = 2.0
LABEL_SETS = ("boons", "keepsakes", "weapons", "gods", "bosses", "knowledge")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Una pregunta no es una declaración: «¿cojo X?», «qué tal X», «should I take X».
_QUESTION_START = re.compile(
    r"^(que|cual|cuales|como|cuando|donde|por que|deberia|debo|merece|es mejor|what|which|how|when|where|why|"
    r"should|is|are|do|does|can|would)\b"
)
_NEGATION = re.compile(r"\b(no|not|never|nunca|sin)\b")


def pretty(identifier: str) -> str:
    """«ApolloWeaponBoon» → «Apollo Weapon Boon», para ids que no están en el vocabulario."""
    return _CAMEL.sub(" ", identifier.replace("_", " ")).strip()


class EvidenceService:
    def __init__(
        self,
        bus: EventBus,
        *,
        config: Callable[[], AppConfig],
        vocabulary: Callable[[], Vocabulary],
    ) -> None:
        self._bus = bus
        self._config = config
        self._vocabulary = vocabulary
        self.ledger = Ledger()
        self.profile: LoadedProfile | None = None
        self.reader: SaveReader | None = None
        self.folder: Path | None = None
        self.error: str = ""
        self.untrusted: UntrustedPluginError | None = None
        self._stamp: tuple[str, float, int] | None = None
        self._tasks: list[asyncio.Task[None]] = []

    # Ciclo de vida -------------------------------------------------------------------------

    async def open(self, loaded: LoadedProfile | None) -> None:
        await self.close()
        self.ledger = Ledger()
        self.profile, self.reader, self.folder = loaded, None, None
        self.error, self.untrusted, self._stamp = "", None, None
        self._tasks.append(asyncio.create_task(self._listen(), name="evidence"))
        if loaded is None or not loaded.profile.evidence.save_reader:
            self._publish()
            return
        try:
            self.reader = save_reader(loaded, self._config().trusted_plugins)
        except UntrustedPluginError as exc:
            self.untrusted, self.error = exc, str(exc)
        except PluginError as exc:
            self.error = str(exc)
        if self.reader is not None:
            self._tasks.append(asyncio.create_task(self._poll(), name="save-watch"))
        else:
            self._status("error", self.error)
        self._publish()

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    def _status(self, state: str, detail: str) -> None:
        self._bus.publish(StatusChanged(component="save", state=state, detail=detail))  # type: ignore[arg-type]

    def _publish(self) -> None:
        snapshot = self.ledger.snapshot()
        snapshot["save"]["folder"] = str(self.folder) if self.folder else None
        snapshot["save"]["error"] = self.error
        snapshot["save"]["reader"] = bool(self.reader)
        self._bus.publish(EvidenceChanged(snapshot=snapshot))

    # Nombres --------------------------------------------------------------------------------

    def label(self, identifier: str) -> str:
        vocabulary = self._vocabulary()
        language = self._config().output_language
        for set_name in LABEL_SETS:
            entry = vocabulary.get(set_name, identifier)
            if entry is not None:
                return entry.display(language)
        return pretty(identifier)

    # Guardado -------------------------------------------------------------------------------

    def _save_folder(self) -> Path | None:
        if self.profile is None:
            return None
        configured = self._config().save_dirs.get(self.profile.profile.id)
        paths = self.profile.profile.evidence.save_paths
        options = paths.windows if sys.platform == "win32" else paths.macos if sys.platform == "darwin" else paths.linux
        for raw in [configured, *options]:
            if raw:
                path = expand_path(raw)
                if path.is_dir():
                    return path
        return None

    async def _poll(self) -> None:
        reader = self.reader
        assert reader is not None
        while True:
            try:
                await self.check_save(reader)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Error leyendo el guardado")
                self.error = str(exc)
                self._status("error", self.error)
            await asyncio.sleep(POLL_S)

    def forget_save(self) -> None:
        """Fuerza a releer el guardado aunque no haya cambiado."""
        self._stamp = None

    async def check_save(self, reader: SaveReader) -> bool:
        """Lee el guardado si ha cambiado. Devuelve si se aplicó una foto nueva."""
        folder = self._save_folder()
        if folder != self.folder:
            self.folder, self._stamp = folder, None
            self._publish()
        if folder is None:
            self._status("waiting", "No encuentro la carpeta de guardados; elígela en la página Juego")
            return False
        path = await asyncio.to_thread(reader.find, folder)
        if path is None:
            self._status("waiting", "Aún no hay guardados en la carpeta")
            return False
        stat = path.stat()
        stamp = (str(path), stat.st_mtime, stat.st_size)
        if stamp == self._stamp:
            return False
        try:
            snapshot = await asyncio.to_thread(reader.read, path)
        except (ValueError, OSError) as exc:
            # Casi siempre es el juego escribiendo: se reintenta en la siguiente vuelta.
            log.debug("Guardado no legible todavía: %s", exc)
            return False
        self._stamp = stamp
        self.apply(snapshot)
        self.error = ""
        place = "en partida" if snapshot.in_run else "en el hub"
        self._status("ready", f"{path.name} · {place} · {time.strftime('%H:%M:%S')}")
        return True

    def apply(self, snapshot: SaveSnapshot) -> None:
        facts = [
            Fact(
                key=f.key, kind=f.kind, subject=f.subject, label=self.label(f.subject), rank=Rank.SAVE,
                value=f.value, details=dict(f.details), run=snapshot.run_id, evidence=Path(snapshot.path).name,
            )
            for f in snapshot.facts
        ]  # fmt: skip
        summary = dict(snapshot.summary)
        choices = []
        for choice in summary.get("choices") or []:
            chosen = choice.get("chosen")
            choices.append({**choice, "label": self.label(chosen) if isinstance(chosen, str) else None})
        summary["choices"] = choices
        summary["in_run"] = snapshot.in_run
        self.ledger.apply_save(snapshot.run_id, facts, summary, snapshot.saved_at, snapshot.path)
        self._publish()

    # Pantallas y jugador ---------------------------------------------------------------------

    async def _listen(self) -> None:
        async with self._bus.subscribe({"perception.screen", "assistant.question"}) as sub:
            async for event in sub:
                try:
                    if isinstance(event, ScreenDetected):
                        self.on_screen(event)
                    elif isinstance(event, QuestionAsked):
                        self.on_player(event.text)
                except Exception:
                    log.exception("Error registrando evidencia")

    def _items(self, event: ScreenDetected) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for value in event.data.values():
            if isinstance(value, list):
                items.extend(i for i in value if isinstance(i, dict))
        return items

    @staticmethod
    def _identity(item: dict[str, Any]) -> tuple[str, str] | None:
        name = item.get("name")
        if isinstance(name, dict) and isinstance(name.get("id"), str):
            return name["id"], str(name.get("name") or name["id"])
        if isinstance(name, str) and name.strip():
            return normalize(name).replace(" ", "_"), name.strip()
        return None

    def on_screen(self, event: ScreenDetected) -> None:
        if event.change == "leave" or not event.screen_id or self.profile is None:
            return
        screen = next((s for s in self.profile.profile.screens if s.id == event.screen_id), None)
        if screen is None or screen.evidence == "none":
            return
        items = [(item, ident) for item in self._items(event) if (ident := self._identity(item)) is not None]
        if screen.evidence == "offered":
            offers = []
            for item, (subject, label) in items:
                rarity = item.get("rarity")
                details = {"rarity": rarity.get("name") if isinstance(rarity, dict) else rarity} if rarity else {}
                offers.append(Fact(f"offer:{subject}", "offer", subject, label, Rank.OFFERED, details=details,
                                   evidence=event.screen_id))  # fmt: skip
            self.ledger.set_offers(offers)
        else:
            # Pantallas que muestran lo que se tiene: el seguidor ya exige dos lecturas coincidentes.
            for _, (subject, label) in items:
                self.ledger.add(Fact(f"item:{subject}", "item", subject, label, Rank.SEEN, evidence=event.screen_id))
        self._publish()

    def on_player(self, text: str) -> Fact | None:
        """«He cogido el Latigazo» tras una oferta: el jugador lo confirma (rango 2)."""
        normalized = normalize(text)
        if "?" in text or "¿" in text or _QUESTION_START.match(normalized) or _NEGATION.search(normalized):
            return None
        mentioned = [offer for offer in self.ledger.offers() if self._mentions(normalized, offer)]
        if len(mentioned) != 1:
            return None  # ninguna o varias: mejor no adivinar
        offer = mentioned[0]
        kind = "boon" if offer.subject.endswith("Boon") else "item"
        fact = Fact(f"{kind}:{offer.subject}", kind, offer.subject, offer.label, Rank.PLAYER,
                    details=dict(offer.details), evidence=text[:200])  # fmt: skip
        if self.ledger.add(fact):
            self._publish()
            return fact
        return None

    def _mentions(self, normalized_text: str, offer: Fact) -> bool:
        label = normalize(offer.label)
        if label and f" {label} " in f" {normalized_text} ":
            return True
        vocabulary = self._vocabulary()
        return any(m.entry.id == offer.subject for m in vocabulary.find("boons", normalized_text))
