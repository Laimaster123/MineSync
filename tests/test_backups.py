from pathlib import Path

import pytest

from blocksync.core.backups import (
    list_backups,
    prune_backups,
    replace_world,
    restore_backup,
    validate_keep,
)
from blocksync.core.staging import cleanup_leftovers, create_staging_dir

from helpers import leftovers, make_world, read_world


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    """Папки, как в настоящем приложении: saves, бэкапы и временные."""
    return {
        "saves": tmp_path / "saves",
        "backups": tmp_path / "app" / "backups",
        "tmp": tmp_path / "app" / "tmp",
    }


def test_replace_world_moves_old_world_to_backup(dirs: dict[str, Path], tmp_path: Path) -> None:
    world = make_world(dirs["saves"] / "W", {"level.dat": b"old"})
    prepared = make_world(tmp_path / "prepared", {"level.dat": b"new"})

    backup_path = replace_world(prepared, world, dirs["backups"], keep=3)

    assert read_world(world) == {"level.dat": b"new"}
    assert backup_path is not None and read_world(backup_path) == {"level.dat": b"old"}
    assert not prepared.exists()


def test_replace_missing_world_makes_no_backup(dirs: dict[str, Path], tmp_path: Path) -> None:
    prepared = make_world(tmp_path / "prepared", {"level.dat": b"new"})

    assert replace_world(prepared, dirs["saves"] / "W", dirs["backups"], keep=3) is None
    assert list_backups(dirs["backups"], "W") == []


def test_only_last_backups_are_kept(dirs: dict[str, Path], tmp_path: Path) -> None:
    world = make_world(dirs["saves"] / "W", {"v": b"0"})
    for version in range(1, 5):
        prepared = make_world(tmp_path / f"prepared{version}", {"v": str(version).encode()})
        replace_world(prepared, world, dirs["backups"], keep=3)

    backups = list_backups(dirs["backups"], "W")

    # Четыре замены, хранится три бэкапа: самый старый (версия 0) удалён.
    assert [read_world(b.path)["v"] for b in backups] == [b"3", b"2", b"1"]


def test_prune_ignores_foreign_folders(dirs: dict[str, Path]) -> None:
    make_world(dirs["backups"] / "W" / "not-a-backup", {"x": b"x"})
    for name in ["2026-01-01_10-00-00", "2026-01-02_10-00-00", "2026-01-03_10-00-00"]:
        make_world(dirs["backups"] / "W" / name, {"x": b"x"})

    removed = prune_backups(dirs["backups"], "W", keep=2)

    assert [b.path.name for b in removed] == ["2026-01-01_10-00-00"]
    assert (dirs["backups"] / "W" / "not-a-backup").exists()


def test_restore_returns_previous_state_and_saves_current(dirs: dict[str, Path], tmp_path: Path) -> None:
    world = make_world(dirs["saves"] / "W", {"level.dat": b"old", "region/a": b"1"})
    prepared = make_world(tmp_path / "prepared", {"level.dat": b"new"})
    replace_world(prepared, world, dirs["backups"], keep=3)
    old_backup = list_backups(dirs["backups"], "W")[0]

    saved_current = restore_backup(old_backup, world, dirs["tmp"], dirs["backups"], keep=3)

    assert read_world(world) == {"level.dat": b"old", "region/a": b"1"}
    assert saved_current is not None and read_world(saved_current) == {"level.dat": b"new"}
    assert old_backup.path.exists()  # выбранный бэкап остался
    assert leftovers(dirs["tmp"]) == []  # временных папок не осталось


@pytest.mark.parametrize("keep", [1, 11])
def test_keep_must_be_between_2_and_10(keep: int) -> None:
    with pytest.raises(ValueError):
        validate_keep(keep)


def test_cleanup_leftovers_removes_only_staging_dirs(dirs: dict[str, Path]) -> None:
    create_staging_dir(dirs["tmp"])
    create_staging_dir(dirs["tmp"])
    other = make_world(dirs["tmp"] / "other", {"x": b"x"})

    assert cleanup_leftovers(dirs["tmp"]) == 2
    assert leftovers(dirs["tmp"]) == [other]
