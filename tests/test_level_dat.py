import gzip
import struct
from pathlib import Path

import pytest

from blocksync.core.level_dat import LevelDatError, read_level_info
from blocksync.core.nbt import NbtError, parse_nbt

from helpers import make_level_dat, nbt_string


def test_read_level_info(tmp_path: Path) -> None:
    path = make_level_dat(tmp_path, level_name="Мой мир", version_name="26.3", data_version=4500)

    info = read_level_info(path)

    assert info.level_name == "Мой мир"
    assert info.version_name == "26.3"
    assert info.data_version == 4500


def test_missing_level_dat_raises(tmp_path: Path) -> None:
    with pytest.raises(LevelDatError):
        read_level_info(tmp_path / "level.dat")


def test_not_gzip_raises(tmp_path: Path) -> None:
    path = tmp_path / "level.dat"
    path.write_bytes(b"not a gzip file")
    with pytest.raises(LevelDatError):
        read_level_info(path)


def test_truncated_nbt_raises(tmp_path: Path) -> None:
    path = make_level_dat(tmp_path)
    raw = gzip.decompress(path.read_bytes())
    path.write_bytes(gzip.compress(raw[:20]))
    with pytest.raises(LevelDatError):
        read_level_info(path)


def test_parse_all_tag_types() -> None:
    # Собираем вручную словарь со всеми типами тегов, которые встречаются в level.dat.
    body = (
        b"\x01" + nbt_string("byte") + struct.pack(">b", -5)
        + b"\x02" + nbt_string("short") + struct.pack(">h", 300)
        + b"\x04" + nbt_string("long") + struct.pack(">q", 2**40)
        + b"\x05" + nbt_string("float") + struct.pack(">f", 0.5)
        + b"\x06" + nbt_string("double") + struct.pack(">d", 1.25)
        + b"\x07" + nbt_string("bytes") + struct.pack(">i", 2) + b"\x01\x02"
        + b"\x09" + nbt_string("list") + b"\x08" + struct.pack(">i", 2) + nbt_string("a") + nbt_string("b")
        + b"\x0b" + nbt_string("ints") + struct.pack(">i", 2) + struct.pack(">ii", 7, 8)
        + b"\x0c" + nbt_string("longs") + struct.pack(">i", 1) + struct.pack(">q", 9)
        + b"\x00"
    )
    data = b"\x0a" + nbt_string("") + body

    assert parse_nbt(data) == {
        "byte": -5,
        "short": 300,
        "long": 2**40,
        "float": 0.5,
        "double": 1.25,
        "bytes": b"\x01\x02",
        "list": ["a", "b"],
        "ints": [7, 8],
        "longs": [9],
    }


def test_root_must_be_compound() -> None:
    with pytest.raises(NbtError):
        parse_nbt(b"\x08" + nbt_string("") + nbt_string("x"))


def test_badges_old_format(tmp_path: Path) -> None:
    path = make_level_dat(tmp_path, extra={"GameType": 1, "hardcore": 1, "Time": 72000})
    info = read_level_info(path)
    assert (info.game_type, info.hardcore, info.play_ticks) == (1, True, 72000)


def test_badges_new_format(tmp_path: Path) -> None:
    # В 26.x «хардкор» лежит в Data.difficulty_settings (так у реального мира 26.3).
    path = make_level_dat(tmp_path, extra={
        "GameType": 0, "Time": 117404, "difficulty_settings": {"hardcore": 0, "difficulty": "hard"},
    })
    info = read_level_info(path)
    assert (info.game_type, info.hardcore, info.play_ticks) == (0, False, 117404)


def test_badges_missing(tmp_path: Path) -> None:
    info = read_level_info(make_level_dat(tmp_path))
    assert (info.game_type, info.hardcore, info.play_ticks) == (None, False, None)
