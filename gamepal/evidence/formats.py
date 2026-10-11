"""Decodificadores de formatos binarios que usan algunos guardados: bloques LZ4 y LuaBins.

Solo leen datos: nunca ejecutan código del juego. Todos tienen límites de tamaño y profundidad
para que un fichero corrupto o malicioso no agote la memoria.
"""

from __future__ import annotations

import struct
from typing import Any

MAX_DECOMPRESSED = 256 * 1024 * 1024
MAX_DEPTH = 200
MAX_ITEMS = 5_000_000


class FormatError(ValueError):
    pass


def lz4_block_decompress(data: bytes, max_size: int = MAX_DECOMPRESSED) -> bytes:
    """Bloque LZ4 crudo (sin cabecera de frame)."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        token = data[i]
        i += 1
        literals = token >> 4
        if literals == 15:
            while True:
                if i >= n:
                    raise FormatError("LZ4: longitud de literales truncada")
                extra = data[i]
                i += 1
                literals += extra
                if extra != 255:
                    break
        if i + literals > n:
            raise FormatError("LZ4: literales fuera del bloque")
        out += data[i : i + literals]
        i += literals
        if i >= n:
            break  # el último bloque de secuencia solo tiene literales
        if i + 2 > n:
            raise FormatError("LZ4: desplazamiento truncado")
        offset = data[i] | (data[i + 1] << 8)
        i += 2
        if offset == 0 or offset > len(out):
            raise FormatError("LZ4: desplazamiento no válido")
        match = token & 0x0F
        if match == 15:
            while True:
                if i >= n:
                    raise FormatError("LZ4: longitud de coincidencia truncada")
                extra = data[i]
                i += 1
                match += extra
                if extra != 255:
                    break
        match += 4
        if len(out) + match > max_size:
            raise FormatError("LZ4: el resultado supera el tamaño permitido")
        start = len(out) - offset
        if offset >= match:
            out += out[start : start + match]
        else:
            # Coincidencia solapada: se copia por tramos para repetir el patrón.
            for k in range(match):
                out.append(out[start + k])
    return bytes(out)


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.items = 0

    def take(self, size: int) -> bytes:
        end = self.pos + size
        if size < 0 or end > len(self.data):
            raise FormatError("Datos truncados")
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int(struct.unpack("<H", self.take(2))[0])

    def u32(self) -> int:
        return int(struct.unpack("<I", self.take(4))[0])

    def u64(self) -> int:
        return int(struct.unpack("<Q", self.take(8))[0])

    def f64(self) -> float:
        return float(struct.unpack("<d", self.take(8))[0])

    def string(self) -> str:
        return self.take(self.u32()).decode("utf-8", errors="replace")


def _luabins_value(reader: _Reader, depth: int) -> Any:
    if depth > MAX_DEPTH:
        raise FormatError("LuaBins: anidamiento excesivo")
    reader.items += 1
    if reader.items > MAX_ITEMS:
        raise FormatError("LuaBins: demasiados elementos")
    kind = reader.u8()
    if kind == ord("-"):
        return None
    if kind == ord("0"):
        return False
    if kind == ord("1"):
        return True
    if kind == ord("N"):
        number = reader.f64()
        return int(number) if number.is_integer() and abs(number) < 2**53 else number
    if kind == ord("S"):
        return reader.string()
    if kind == ord("T"):
        total = reader.u32() + reader.u32()
        if total > len(reader.data):
            raise FormatError("LuaBins: tabla imposible")
        table: dict[Any, Any] = {}
        for _ in range(total):
            key = _luabins_value(reader, depth + 1)
            value = _luabins_value(reader, depth + 1)
            if isinstance(key, dict):
                raise FormatError("LuaBins: clave no válida")
            table[key] = value
        return table
    raise FormatError(f"LuaBins: tipo desconocido {kind!r}")


def luabins_loads(data: bytes) -> list[Any]:
    """Lista de valores de Lua: tablas como dict, números enteros como int."""
    reader = _Reader(data)
    count = reader.u8()
    if count > 250:
        raise FormatError("LuaBins: demasiados valores")
    values = [_luabins_value(reader, 0) for _ in range(count)]
    if reader.pos != len(data):
        raise FormatError("LuaBins: datos sobrantes")
    return values


def lua_list(table: Any) -> list[Any]:
    """Una tabla de Lua usada como lista (claves 1..n) convertida a lista de Python."""
    if not isinstance(table, dict):
        return []
    keys = sorted(k for k in table if isinstance(k, int))
    return [table[k] for k in keys]
