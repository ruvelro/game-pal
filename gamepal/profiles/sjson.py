"""Lector tolerante de SJSON, el formato de datos de texto que usan algunos juegos (p. ej. Hades II).

SJSON se parece a JSON sin comillas en las claves, con `=` en lugar de `:`, comas opcionales y
comentarios `//` y `/* */`. Solo se leen datos; nunca se ejecuta nada.
"""

from __future__ import annotations

from typing import Any

MAX_DEPTH = 64
MAX_ITEMS = 500_000


class SjsonError(ValueError):
    pass


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.items = 0

    def error(self, message: str) -> SjsonError:
        line = self.text.count("\n", 0, self.pos) + 1
        return SjsonError(f"{message} (línea {line})")

    def skip(self) -> None:
        text, n = self.text, len(self.text)
        while self.pos < n:
            ch = text[self.pos]
            if ch in " \t\r\n,;﻿":
                self.pos += 1
            elif text.startswith("//", self.pos):
                end = text.find("\n", self.pos)
                self.pos = n if end < 0 else end + 1
            elif text.startswith("/*", self.pos):
                end = text.find("*/", self.pos + 2)
                if end < 0:
                    raise self.error("Comentario sin cerrar")
                self.pos = end + 2
            else:
                return

    def value(self, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise self.error("Anidamiento excesivo")
        self.items += 1
        if self.items > MAX_ITEMS:
            raise self.error("Demasiados elementos")
        self.skip()
        if self.pos >= len(self.text):
            raise self.error("Fin inesperado")
        ch = self.text[self.pos]
        if ch == "{":
            return self.obj(depth)
        if ch == "[":
            return self.arr(depth)
        if ch in "\"'":
            return self.string(ch)
        return self.bare()

    def obj(self, depth: int) -> dict[str, Any]:
        self.pos += 1
        result: dict[str, Any] = {}
        while True:
            self.skip()
            if self.pos >= len(self.text):
                raise self.error("Objeto sin cerrar")
            if self.text[self.pos] == "}":
                self.pos += 1
                return result
            key = self.string(self.text[self.pos]) if self.text[self.pos] in "\"'" else self.word()
            self.skip()
            if self.pos < len(self.text) and self.text[self.pos] in "=:":
                self.pos += 1
            else:
                raise self.error(f"Falta '=' tras la clave {key!r}")
            result[key] = self.value(depth + 1)

    def arr(self, depth: int) -> list[Any]:
        self.pos += 1
        result: list[Any] = []
        while True:
            self.skip()
            if self.pos >= len(self.text):
                raise self.error("Lista sin cerrar")
            if self.text[self.pos] == "]":
                self.pos += 1
                return result
            result.append(self.value(depth + 1))

    def string(self, quote: str) -> str:
        # Cadenas triples ("""...""") para texto multilínea.
        if self.text.startswith(quote * 3, self.pos):
            end = self.text.find(quote * 3, self.pos + 3)
            if end < 0:
                raise self.error("Cadena sin cerrar")
            value = self.text[self.pos + 3 : end]
            self.pos = end + 3
            return value
        self.pos += 1
        out: list[str] = []
        escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\", "/": "/"}
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == "\\" and self.pos + 1 < len(self.text):
                nxt = self.text[self.pos + 1]
                if nxt == "u" and self.pos + 5 < len(self.text):
                    out.append(chr(int(self.text[self.pos + 2 : self.pos + 6], 16)))
                    self.pos += 6
                    continue
                out.append(escapes.get(nxt, nxt))
                self.pos += 2
                continue
            if ch == quote:
                self.pos += 1
                return "".join(out)
            out.append(ch)
            self.pos += 1
        raise self.error("Cadena sin cerrar")

    def word(self) -> str:
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] in "_.$-+"):
            self.pos += 1
        if start == self.pos:
            raise self.error(f"Carácter inesperado {self.text[self.pos]!r}")
        return self.text[start : self.pos]

    def bare(self) -> Any:
        token = self.word()
        lowered = token.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered in {"null", "nil"}:
            return None
        try:
            return int(token)
        except ValueError:
            pass
        try:
            return float(token)
        except ValueError:
            return token


def loads(text: str) -> Any:
    probe = _Parser(text)
    probe.skip()
    # Un fichero puede ser el cuerpo de un objeto sin llaves exteriores: «Texts = [ ... ]».
    if probe.pos < len(text) and text[probe.pos] not in "{[":
        text = "{" + text + "\n}"
    parser = _Parser(text)
    result = parser.value(0)
    parser.skip()
    if parser.pos < len(text):
        raise parser.error("Contenido sobrante")
    return result
