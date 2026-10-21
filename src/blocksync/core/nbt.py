"""Минимальный читатель формата NBT, в котором Minecraft хранит данные.

NBT (Named Binary Tag) — двоичный формат: дерево из «тегов». У каждого тега есть
тип (число, строка, список, словарь и т.д.), имя и значение. Все числа
записаны в порядке big-endian (старший байт первым).

Здесь мы только читаем: превращаем байты в обычные значения Python
(int, float, str, list, dict). Описание формата: https://minecraft.wiki/w/NBT_format
"""

import struct
from typing import Any

# Номера типов тегов из описания формата.
TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

# Формат struct для простых числовых тегов: ">" значит big-endian,
# b/h/i/q — целые 1/2/4/8 байт, f/d — дробные 4/8 байт.
_NUMBER_FORMATS: dict[int, str] = {
    TAG_BYTE: ">b",
    TAG_SHORT: ">h",
    TAG_INT: ">i",
    TAG_LONG: ">q",
    TAG_FLOAT: ">f",
    TAG_DOUBLE: ">d",
}


class NbtError(Exception):
    """Данные не похожи на правильный NBT."""


class _Reader:
    """Читает значения из байтов по порядку, запоминая текущую позицию."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def unpack(self, fmt: str) -> Any:
        """Прочитать одно значение по формату struct и сдвинуть позицию."""
        size = struct.calcsize(fmt)
        if self.pos + size > len(self.data):
            raise NbtError("данные NBT неожиданно закончились")
        (value,) = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return value

    def read_bytes(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise NbtError("данные NBT неожиданно закончились")
        chunk = self.data[self.pos : self.pos + count]
        self.pos += count
        return chunk

    def read_string(self) -> str:
        """Строка: длина (2 байта), затем текст в UTF-8."""
        length = self.unpack(">H")
        return self.read_bytes(length).decode("utf-8", errors="replace")

    def read_payload(self, tag_type: int) -> Any:
        """Прочитать значение тега заданного типа."""
        if tag_type in _NUMBER_FORMATS:
            return self.unpack(_NUMBER_FORMATS[tag_type])
        if tag_type == TAG_STRING:
            return self.read_string()
        if tag_type == TAG_BYTE_ARRAY:
            return self.read_bytes(self.unpack(">i"))
        if tag_type == TAG_INT_ARRAY:
            return [self.unpack(">i") for _ in range(self.unpack(">i"))]
        if tag_type == TAG_LONG_ARRAY:
            return [self.unpack(">q") for _ in range(self.unpack(">i"))]
        if tag_type == TAG_LIST:
            # Список: тип элементов (1 байт), количество (4 байта), элементы.
            item_type = self.unpack(">b")
            count = self.unpack(">i")
            return [self.read_payload(item_type) for _ in range(count)]
        if tag_type == TAG_COMPOUND:
            # Словарь: пары «тип, имя, значение» до тега TAG_END.
            result: dict[str, Any] = {}
            while True:
                child_type = self.unpack(">b")
                if child_type == TAG_END:
                    return result
                name = self.read_string()
                result[name] = self.read_payload(child_type)
        raise NbtError(f"неизвестный тип тега: {tag_type}")


def parse_nbt(data: bytes) -> dict[str, Any]:
    """Разобрать распакованные байты NBT. Корень всегда словарь (compound)."""
    reader = _Reader(data)
    if reader.unpack(">b") != TAG_COMPOUND:
        raise NbtError("корневой тег NBT должен быть словарём")
    reader.read_string()  # имя корня, обычно пустое
    return reader.read_payload(TAG_COMPOUND)
