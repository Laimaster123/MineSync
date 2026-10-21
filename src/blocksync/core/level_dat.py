"""Чтение сведений о мире из файла level.dat.

level.dat — это данные в формате NBT, сжатые gzip (как zip, но для одного файла).
Нам нужны название мира, версия игры и немного сведений для значков:
режим игры, «хардкор» и сколько времени проведено в мире.
"""

import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blocksync.core.nbt import NbtError, parse_nbt


class LevelDatError(Exception):
    """level.dat отсутствует или повреждён."""


@dataclass(frozen=True)
class LevelInfo:
    level_name: str | None  # название мира, которое видно в игре
    version_name: str | None  # версия игры, например "1.21.4"
    data_version: int | None  # номер формата данных; чем больше, тем новее версия
    game_type: int | None = None  # режим: 0 выживание, 1 творческий, 2 приключение, 3 наблюдатель
    hardcore: bool = False
    play_ticks: int | None = None  # сколько игровых тиков мир был открыт (20 тиков = 1 секунда)


def read_level_info(level_dat: Path) -> LevelInfo:
    """Прочитать сведения о мире из level.dat."""
    try:
        raw = gzip.decompress(level_dat.read_bytes())
        root = parse_nbt(raw)
    except (OSError, EOFError, NbtError) as error:
        # gzip.BadGzipFile — подвид OSError.
        raise LevelDatError(f"не удалось прочитать {level_dat}: {error}") from error

    data = root.get("Data")
    if not isinstance(data, dict):
        raise LevelDatError(f"в {level_dat} нет раздела Data")

    version = data.get("Version")
    version_name = version.get("Name") if isinstance(version, dict) else None
    return LevelInfo(
        level_name=_as_type(data.get("LevelName"), str),
        version_name=_as_type(version_name, str),
        data_version=_as_type(data.get("DataVersion"), int),
        game_type=_as_type(data.get("GameType"), int),
        hardcore=_is_hardcore(data),
        play_ticks=_as_type(data.get("Time"), int),
    )


def _is_hardcore(data: dict[str, Any]) -> bool:
    """В старых версиях флаг лежит прямо в Data, в новых (26.x) — в Data.difficulty_settings."""
    settings = data.get("difficulty_settings")
    if isinstance(settings, dict) and isinstance(settings.get("hardcore"), int):
        return bool(settings["hardcore"])
    return bool(_as_type(data.get("hardcore"), int))


def _as_type(value: Any, expected: type) -> Any:
    """Вернуть значение, если оно нужного типа, иначе None."""
    return value if isinstance(value, expected) else None
