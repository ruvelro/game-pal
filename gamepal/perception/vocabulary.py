"""Vocabulario del juego: entidades conocidas con sus nombres en cada idioma.

Sirve para tres cosas: reconocer pantallas, corregir errores de OCR y limitar lo que un modelo
puede nombrar. Se carga de ficheros YAML del perfil y, si el juego está instalado, de sus
propios ficheros de texto, que traen los nombres exactos en todos los idiomas.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

from gamepal.perception.text import max_edits, normalize
from gamepal.profiles import sjson
from gamepal.profiles.model import GameProfile, GameTextSource

log = logging.getLogger(__name__)

MAX_VOCAB_BYTES = 4 * 1024 * 1024
MAX_GAME_TEXT_BYTES = 32 * 1024 * 1024
MAX_NGRAM = 6
# Marcas de formato habituales en textos de juegos: {$Keywords.X}, {!Icons.X}, {#Bold}, [color]…
_MARKUP = re.compile(r"\{[^}]*\}|\[[^\]]*\]|<[^>]*>")


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    set: str
    names: dict[str, str]
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def display(self, language: str) -> str:
        return self.names.get(language) or self.names.get("en") or next(iter(self.names.values()), self.id)


@dataclass(frozen=True, slots=True)
class Match:
    entry: Entry
    form: str  # forma normalizada que casó
    distance: int
    start: int  # posición en palabras dentro de la línea
    length: int

    @property
    def score(self) -> float:
        return 1.0 - self.distance / (len(self.form) + 1)


@dataclass(slots=True)
class VocabularySource:
    set: str
    origin: str
    entries: int


class Vocabulary:
    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Entry]] = {}
        # set → forma normalizada → entradas que la usan
        self._forms: dict[str, dict[str, list[Entry]]] = {}
        # set → número de palabras → formas, para comparar en bloque con rapidfuzz
        self._by_words: dict[str, dict[int, list[str]]] = {}
        self.sources: list[VocabularySource] = []

    # Construcción ----------------------------------------------------------------------------

    def add(self, entry: Entry) -> None:
        existing = self._entries.setdefault(entry.set, {}).get(entry.id)
        if existing is not None:
            names = {**existing.names, **entry.names}
            aliases = {**existing.aliases, **entry.aliases}
            entry = Entry(entry.id, entry.set, names, aliases)
        self._entries[entry.set][entry.id] = entry
        forms = self._forms.setdefault(entry.set, {})
        for text in self._texts(entry):
            form = normalize(text)
            if len(form) >= 2:
                bucket = forms.setdefault(form, [])
                bucket[:] = [e for e in bucket if e.id != entry.id]
                bucket.append(entry)
        self._by_words.pop(entry.set, None)

    @staticmethod
    def _texts(entry: Entry) -> Iterable[str]:
        yield from entry.names.values()
        for aliases in entry.aliases.values():
            yield from aliases

    # Consulta --------------------------------------------------------------------------------

    def sets(self) -> dict[str, int]:
        return {name: len(entries) for name, entries in sorted(self._entries.items())}

    def has_set(self, name: str) -> bool:
        return bool(self._entries.get(name))

    def entries(self, name: str) -> list[Entry]:
        return list(self._entries.get(name, {}).values())

    def get(self, set_name: str, entry_id: str) -> Entry | None:
        return self._entries.get(set_name, {}).get(entry_id)

    def _grouped(self, set_name: str) -> dict[int, list[str]]:
        grouped = self._by_words.get(set_name)
        if grouped is None:
            grouped = {}
            for form in self._forms.get(set_name, {}):
                grouped.setdefault(len(form.split()), []).append(form)
            self._by_words[set_name] = grouped
        return grouped

    def find(self, set_name: str, text: str, fuzzy: int | None = None) -> list[Match]:
        """Entradas del conjunto que aparecen en `text`, de mejor a peor, sin solapes."""
        forms = self._forms.get(set_name)
        if not forms:
            return []
        words = normalize(text).split()
        if not words:
            return []
        candidates: list[Match] = []
        for count, group in self._grouped(set_name).items():
            if count > MAX_NGRAM:
                continue
            # Un error de OCR puede partir o juntar palabras: se prueban tamaños vecinos.
            for size in {count - 1, count, count + 1} - {0}:
                for start in range(0, len(words) - size + 1):
                    chunk = " ".join(words[start : start + size])
                    candidates.extend(self._best(forms, group, chunk, start, size, fuzzy))
        candidates.sort(key=lambda m: (m.distance, -len(m.form)))
        chosen: list[Match] = []
        taken: set[int] = set()
        for match in candidates:
            span = set(range(match.start, match.start + match.length))
            if span & taken:
                continue
            chosen.append(match)
            taken |= span
        return chosen

    @staticmethod
    def _best(
        forms: dict[str, list[Entry]], group: list[str], chunk: str, start: int, size: int, fuzzy: int | None
    ) -> Iterable[Match]:
        exact = forms.get(chunk)
        if exact and chunk in group:
            yield Match(exact[0], chunk, 0, start, size)
            return
        limit = fuzzy if fuzzy is not None else max_edits(len(chunk) + 2)
        if limit == 0:
            return
        hits = process.extract(chunk, group, scorer=Levenshtein.distance, score_cutoff=limit, limit=3)
        for form, distance, _ in hits:
            budget = max_edits(len(form)) if fuzzy is None else fuzzy
            if 0 < distance <= budget:
                yield Match(forms[form][0], form, int(distance), start, size)

    def canonical(self, set_name: str, text: str) -> Entry | None:
        """La entrada que mejor representa un texto completo, o None si no hay una clara."""
        matches = self.find(set_name, text)
        return matches[0].entry if matches else None


# Carga --------------------------------------------------------------------------------------


def _names(value: Any, entry_id: str) -> dict[str, str]:
    if isinstance(value, str):
        return {"en": value}
    if isinstance(value, dict):
        return {str(lang): str(name) for lang, name in value.items() if name}
    raise ValueError(f"Nombres no válidos para '{entry_id}'")


def _aliases(value: Any) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if isinstance(value, list):
        return {"*": tuple(str(v) for v in value)}
    if isinstance(value, dict):
        return {
            str(lang): tuple(str(v) for v in (items if isinstance(items, list) else [items]))
            for lang, items in value.items()
        }
    raise ValueError("Alias no válidos")


def load_yaml(vocabulary: Vocabulary, path: Path) -> None:
    if path.stat().st_size > MAX_VOCAB_BYTES:
        raise ValueError(f"{path.name} es demasiado grande")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sets = raw.get("sets") if isinstance(raw, dict) else None
    if not isinstance(sets, dict):
        raise ValueError(f"{path.name} debe tener una clave 'sets'")
    for set_name, items in sets.items():
        if not isinstance(items, list):
            raise ValueError(f"El conjunto '{set_name}' debe ser una lista")
        for item in items:
            if isinstance(item, str):
                entry = Entry(normalize(item).replace(" ", "_") or item, str(set_name), {"*": item})
            elif isinstance(item, dict) and "id" in item:
                entry_id = str(item["id"])
                entry = Entry(
                    entry_id,
                    str(set_name),
                    _names(item.get("names", entry_id), entry_id),
                    _aliases(item.get("aliases")),
                )
            else:
                raise ValueError(f"Entrada no válida en '{set_name}': {item!r}")
            vocabulary.add(entry)
        vocabulary.sources.append(VocabularySource(str(set_name), path.name, len(items)))


def _clean_name(text: str) -> str:
    return " ".join(_MARKUP.sub(" ", text).split())


def _collect(node: Any, id_key: str, name_key: str, out: dict[str, str]) -> None:
    if isinstance(node, dict):
        if id_key in node and isinstance(node.get(name_key), str):
            out[str(node[id_key])] = _clean_name(node[name_key])
        for value in node.values():
            _collect(value, id_key, name_key, out)
    elif isinstance(node, list):
        for value in node:
            _collect(value, id_key, name_key, out)


def load_game_text(vocabulary: Vocabulary, install_dir: Path, source: GameTextSource, languages: dict[str, str]) -> int:
    """Lee los nombres de un fichero de texto del juego en cada idioma disponible."""
    pattern = re.compile(source.id_pattern) if source.id_pattern else None
    per_id: dict[str, dict[str, str]] = {}
    for ours, theirs in languages.items():
        path = _resolve_inside(install_dir, source.path.replace("{lang}", theirs))
        if path is None or not path.is_file():
            continue
        if path.stat().st_size > MAX_GAME_TEXT_BYTES:
            log.warning("%s es demasiado grande; se ignora", path)
            continue
        try:
            data = sjson.loads(path.read_text(encoding="utf-8-sig"))
        except (sjson.SjsonError, UnicodeDecodeError) as exc:
            log.warning("No se pudo leer %s: %s", path, exc)
            continue
        names: dict[str, str] = {}
        _collect(data, source.id_key, source.name_key, names)
        for entry_id, name in names.items():
            if name and (pattern is None or pattern.search(entry_id)):
                per_id.setdefault(entry_id, {})[ours] = name
    for entry_id, names_by_lang in per_id.items():
        vocabulary.add(Entry(entry_id, source.set, names_by_lang))
    if per_id:
        vocabulary.sources.append(VocabularySource(source.set, f"juego: {source.path}", len(per_id)))
    return len(per_id)


def _resolve_inside(root: Path, relative: str) -> Path | None:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate.is_relative_to(root) and candidate.exists():
        return candidate
    # En macOS los recursos de un juego pueden estar dentro del paquete .app.
    for app in sorted(root.glob("*.app")):
        nested = (app / "Contents" / "Resources" / relative).resolve()
        if nested.is_relative_to(root) and nested.exists():
            return nested
    return None


def expand_path(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(raw)))


def find_install_dir(profile: GameProfile, configured: str | None) -> Path | None:
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    paths = profile.game.install_paths
    if sys.platform == "win32":
        candidates += paths.windows
    elif sys.platform == "darwin":
        candidates += paths.macos
    else:
        candidates += paths.linux
    for raw in candidates:
        path = expand_path(raw)
        if path.is_dir():
            return path
    return None


def build_vocabulary(
    profile: GameProfile, profile_root: Path, install_dir: Path | None, languages: Iterable[str]
) -> Vocabulary:
    vocabulary = Vocabulary()
    root = profile_root.resolve()
    for relative in profile.vocabulary.files:
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"El vocabulario '{relative}' sale de la carpeta del perfil")
        load_yaml(vocabulary, path)
    if install_dir is not None:
        mapping = {lang: profile.vocabulary.language_codes.get(lang, lang) for lang in languages}
        for source in profile.vocabulary.game_text:
            load_game_text(vocabulary, install_dir, source, mapping)
    return vocabulary
