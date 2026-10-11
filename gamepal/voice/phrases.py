"""Reconocimiento de frases clave y de parada dentro de una transcripción."""

from __future__ import annotations

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
            return None
        rest = " ".join(original[best[1] :]).lstrip(",.:;!¡¿? ")
        return PhraseHit(best[2], rest)

    def contains(self, transcript: str) -> str | None:
        """Alguna frase en cualquier parte de una transcripción corta."""
        text = f" {normalize(transcript)} "
        for phrase, target in self._phrases:
            if f" {' '.join(target)} " in text:
                return phrase
        return None
