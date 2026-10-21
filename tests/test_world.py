from pathlib import Path

import pytest

from blocksync.core.world import list_worlds, read_world_info
from helpers import BASE_TIME, make_level_dat, make_world, write_file


def test_read_world_info(tmp_path: Path) -> None:
    world = make_world(tmp_path / "saves" / "World1", {"region/r.0.0.mca": b"12345", "icon.png": b"png"})
    make_level_dat(world, level_name="Мой мир", version_name="26.3", data_version=5023)
    write_file(world / "data/new.dat", b"x", mtime=BASE_TIME + 500)

    info = read_world_info(world)

    assert info.folder_name == "World1"
    assert info.display_name == "Мой мир"
    assert info.version_name == "26.3"
    assert info.data_version == 5023
    assert info.size_bytes == 5 + 3 + 1 + (world / "level.dat").stat().st_size
    assert info.last_modified_ns == (BASE_TIME + 500) * 1_000_000_000
    assert info.icon_path == world / "icon.png"
    assert not info.is_locked


def test_broken_level_dat_uses_folder_name(tmp_path: Path) -> None:
    world = make_world(tmp_path / "Сломанный мир", {"level.dat": b"broken"})

    info = read_world_info(world)

    assert info.display_name == "Сломанный мир"
    assert info.version_name is None
    assert info.icon_path is None


def test_list_worlds_sorted_by_last_change(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    make_level_dat(make_world(saves / "Old", {}, mtime=BASE_TIME))
    newer = make_world(saves / "New", {"a.dat": b"x"}, mtime=BASE_TIME + 1000)
    make_level_dat(newer)
    write_file(newer / "a.dat", b"x", mtime=BASE_TIME + 1000)
    make_world(saves / "NotAWorld", {"readme.txt": b"x"})

    assert [w.folder_name for w in list_worlds(saves)] == ["New", "Old"]


def test_missing_saves_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list_worlds(tmp_path / "nope")
