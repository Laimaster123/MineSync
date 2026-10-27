"""Сборки: поиск папки с мирами внутри выбранной папки и понятные причины, если не вышло."""

from pathlib import Path

import pytest

from blocksync.config.instances import (
    InstanceError,
    InstanceProblem,
    find_saves_dir,
    instance_from_saves,
    make_instance,
)

from helpers import make_level_dat, make_world


def make_prism_instance(root: Path, folder: str, name: str | None, game_dir: str = "minecraft") -> Path:
    """Папка сборки как у Prism Launcher: <root>/instances/<folder>/<game_dir>/saves."""
    instance = root / "instances" / folder
    (instance / game_dir / "saves").mkdir(parents=True)
    (instance / "instance.cfg").write_text(
        f"InstanceType=OneSix\nname={name}\n" if name else "InstanceType=OneSix\n", encoding="utf-8"
    )
    return instance


@pytest.mark.parametrize("inside", ["saves", "minecraft/saves", ".minecraft/saves", "profiles/main/saves"])
def test_saves_found_inside_instance(tmp_path: Path, inside: str) -> None:
    folder = tmp_path / "Моя сборка"
    (folder / inside).mkdir(parents=True)
    (folder / "mods" / "deep" / "saves").mkdir(parents=True)  # в mods не заглядываем

    instance = make_instance(folder, [])

    assert instance.saves_dir == folder / inside
    assert instance.name == "Моя сборка"


def test_saves_folder_itself_or_folder_with_worlds(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    saves.mkdir()
    assert find_saves_dir(saves) == saves
    worlds = tmp_path / "мои миры"
    make_level_dat(make_world(worlds / "Мир", {"a": b"1"}))
    assert find_saves_dir(worlds) == worlds


def test_problems_are_explained(tmp_path: Path) -> None:
    with pytest.raises(InstanceError) as missing:
        make_instance(tmp_path / "нет такой", [])
    assert missing.value.problem is InstanceProblem.NOT_A_FOLDER

    world = make_world(tmp_path / "Мир", {"a": b"1"})
    make_level_dat(world)
    with pytest.raises(InstanceError) as is_world:
        make_instance(world, [])
    assert is_world.value.problem is InstanceProblem.IS_WORLD

    empty = tmp_path / "пустая"
    (empty / "mods").mkdir(parents=True)
    with pytest.raises(InstanceError) as no_saves:
        make_instance(empty, [])
    assert no_saves.value.problem is InstanceProblem.NO_SAVES

    folder = make_prism_instance(tmp_path / "Prism", "26.3", "26.3")
    first = make_instance(folder, [])
    with pytest.raises(InstanceError) as again:
        make_instance(folder / "minecraft", [first])
    assert again.value.problem is InstanceProblem.ALREADY_ADDED and again.value.existing == first


def test_old_saves_setting_becomes_instance(tmp_path: Path) -> None:
    saves = make_prism_instance(tmp_path / "Prism", "26.3", "26.3") / "minecraft" / "saves"
    instance = instance_from_saves(saves)
    assert (instance.name, instance.saves_dir) == ("26.3", saves)
