"""Prueba si Piper puede fonetizar en este equipo. Se ejecuta en un proceso aparte (ver tts.piper_works).

Algunas versiones de piper-tts traen espeak-ng con la ruta de sus datos fijada en la máquina donde se
compilaron (piper-tts 1.8.0 en macOS). Al no encontrarlos, espeak-ng termina el proceso entero con exit(1):
si pasara dentro de game-pal, se cerraría la aplicación al hablar.
"""

from __future__ import annotations

import sys


def main() -> int:
    from piper.phonemize_espeak import ESPEAK_DATA_DIR, EspeakPhonemizer

    data_dir = sys.argv[1] if len(sys.argv) > 1 else ESPEAK_DATA_DIR
    EspeakPhonemizer(data_dir).phonemize("en", "test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
