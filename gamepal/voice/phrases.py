"""Reconocimiento de frases clave y de parada dentro de una transcripción."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

from gamepal.perception.text import max_edits, normalize

# Palabras de relleno que Whisper suele añadir antes de la frase clave.
FILLERS = frozenset({"eh", "ey", "oye", "ah", "um", "hm", "pues", "bueno", "ok", "okay", "vale"})


@dataclass(frozen=True, slots=True)
class PhraseHit:
    phrase: str
    rest: str  # lo que se dijo después de la frase


class PhraseMatcher:
    def __init__(self, phrases: list[str], window: int = 2, tolerance: float = 0.25) -> None:
        self.phrases = [phrase for phrase in phrases if normalize(phrase)]
        self._phrases = [(phrase, normalize(phrase).split()) for phrase in self.phrases]
        # Cuántas palabras pueden preceder a la frase (relleno o errores al empezar).
        self._window = window
        # Fracción de letras que pueden estar mal transcritas («hoy compañeros» ≈ «oye compañero»).
        self._tolerance = tolerance

    def match_start(self, transcript: str) -> PhraseHit | None:
        """La frase clave al principio de lo dicho, tolerando errores de transcripción."""
        original = transcript.split()
        words = normalize(transcript).split()
        if len(original) != len(words):
            original = words  # la normalización cambió el troceo; se devuelve el texto normalizado
        best: tuple[int, int, str] | None = None
        for phrase, target in self._phrases:
            joined = " ".join(target)
            budget = max(1, max_edits(len(joined)), round(len(joined) * self._tolerance))
            for start in range(0, min(self._window, len(words)) + 1):
                # Delante solo puede haber una palabra cualquiera (un error al arrancar) o relleno.
                if start > 1 and not all(word in FILLERS for word in words[:start]):
                    continue
                for size in {len(target) - 1, len(target), len(target) + 1} - {0}:
                    chunk = " ".join(words[start : start + size])
                    if not chunk:
                        continue
                    distance = Levenshtein.distance(joined, chunk, score_cutoff=budget)
                    if distance <= budget and (best is None or distance < best[0]):
                        best = (distance, start + size, phrase)
        if best is None:
            anchored = self._anchored(words)
            if anchored is None:
                return None
            best = (0, *anchored)
        rest = " ".join(original[best[1] :]).lstrip(",.:;!¡¿? ")
        return PhraseHit(best[2], rest)

    def _anchored(self, words: list[str]) -> tuple[int, str] | None:
        """La palabra larga de la frase casi exacta, con poco delante: Whisper oye «puede compañero» o «hoy es
        compañero» por «oye compañero». La palabra distintiva basta; lo corto de delante se le perdona."""
        for phrase, target in self._phrases:
            anchor = target[-1]
            if len(target) < 2 or len(anchor) < 6:
                continue
            # Delante tiene que haber algo («oye»): «compañero» a secas al empezar una frase es demasiado común
            # («compañero, está listo» lo activó en una prueba).
            for index in range(1, min(3, len(words))):
                if Levenshtein.distance(anchor, words[index], score_cutoff=1) > 1:
                    continue
                before = words[:index]
                if all(len(word) <= 5 for word in before):
                    return index + 1, phrase
        return None

    def whole(self, transcript: str, extra: int = 1) -> str | None:
        """Una frase que es todo lo dicho, o casi («para», «para ya», «vale, gracias»).

        Las frases de parada son palabras corrientes: «para» sale en casi cualquier respuesta. Si contara en
        cualquier parte, el micrófono que oye la propia voz de game-pal la cortaría a media frase.
        """
        words = [word for word in normalize(transcript).split() if word not in FILLERS]
        for phrase, target in self._phrases:
            if len(target) <= len(words) <= len(target) + extra and all(
                _close(said, wanted) for said, wanted in zip(words, target, strict=False)
            ):
                return phrase
        return None

    def ends(self, transcript: str) -> str | None:
        """Una frase que es la última oración de lo dicho: «…para invierno. Para.».

        Mientras game-pal habla, el micrófono oye su voz y la del jugador en una sola frase; la orden del jugador
        llega al final y Whisper la separa con un punto. Ninguna respuesta acaba en «Para.» sin más.
        """
        sentences = [part for part in re.split(r"[.!?¡¿…]+", transcript) if normalize(part)]
        return self.whole(sentences[-1]) if len(sentences) > 1 else None


def _close(said: str, wanted: str) -> bool:
    """La misma palabra, o casi si es larga: «parar» por «para», «gracia» por «gracias»."""
    if len(wanted) < 4:
        return said == wanted
    return Levenshtein.distance(said, wanted, score_cutoff=1) <= 1
