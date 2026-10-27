"""Быстрая синхронизация: неизменённые файлы не трогаются, обрыв посередине откатывается."""

import os
import unicodedata
from pathlib import Path

import pytest

from blocksync.core import swap
from blocksync.core.apply import LocalFolderSource, apply_sync
from blocksync.core.backups import list_backups, restore_backup
from blocksync.core.compare import compare
from blocksync.core.manifest import build_manifest
from blocksync.core.staging import cleanup_leftovers, create_staging_dir

from helpers import BASE_TIME, leftovers, make_world, read_world, write_file

BIG = b"x" * 200_000


@pytest.fixture
def folders(tmp_path: Path) -> dict[str, Path]:
    return {"source": tmp_path / "pc" / "World", "target": tmp_path / "mac" / "World",
            "tmp": tmp_path / "tmp", "backups": tmp_path / "backups"}


def sync(f: dict[str, Path]) -> None:
    plan = compare(build_manifest(f["source"]), build_manifest(f["target"]))
    apply_sync(plan, LocalFolderSource(f["source"]), f["target"], f["tmp"], f["backups"], 3)


def test_unchanged_files_are_not_copied(folders: dict[str, Path]) -> None:
    make_world(folders["target"], {"region/r.0.0.mca": BIG, "level.dat": b"old", "data/лишний.dat": b"x"})
    make_world(folders["source"], {"region/r.0.0.mca": BIG, "data/новый.dat": b"n"})
    write_file(folders["source"] / "level.dat", b"new", mtime=BASE_TIME + 10)  # изменился только он
    big_before = os.stat(folders["target"] / "region" / "r.0.0.mca")

    sync(folders)

    assert read_world(folders["target"]) == read_world(folders["source"])
    # Большой неизменённый файл — тот же самый файл на диске, его не переписывали.
    big_after = os.stat(folders["target"] / "region" / "r.0.0.mca")
    assert (big_after.st_ino, big_after.st_mtime_ns) == (big_before.st_ino, big_before.st_mtime_ns)
    # В бэкапе только старые версии заменённых и удалённых файлов.
    [backup] = list_backups(folders["backups"], "World")
    assert backup.is_delta
    assert read_world(backup.path / "files") == {"level.dat": b"old", "data/лишний.dat": b"x"}
    assert leftovers(folders["tmp"]) == []


def test_restore_delta_backup_and_undo(folders: dict[str, Path]) -> None:
    make_world(folders["target"], {"level.dat": b"old", "a/старый.dat": b"1"})
    make_world(folders["source"], {"level.dat": b"new", "b/новый.dat": b"2"}, mtime=BASE_TIME + 10)
    before = read_world(folders["target"])
    sync(folders)
    after = read_world(folders["target"])
    assert not (folders["target"] / "a").exists()  # опустевшая папка удалена

    [backup] = list_backups(folders["backups"], "World")
    restore_backup(backup, folders["target"], folders["tmp"], folders["backups"], 3)
    assert read_world(folders["target"]) == before

    # Восстановление тоже можно отменить: его бэкап возвращает состояние после синхронизации.
    newest = list_backups(folders["backups"], "World")[0]
    restore_backup(newest, folders["target"], folders["tmp"], folders["backups"], 3)
    assert read_world(folders["target"]) == after


def test_failure_in_the_middle_is_rolled_back(folders: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    make_world(folders["target"], {"level.dat": b"old", "r1": b"1", "r2": b"2", "gone": b"g"})
    make_world(folders["source"], {"level.dat": b"new", "r1": b"11", "r2": b"22", "added": b"a"},
               mtime=BASE_TIME + 10)
    before = read_world(folders["target"])
    real_move = swap._move
    calls = {"count": 0}

    def flaky_move(source: Path, target: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 6:  # старые версии уже в бэкапе, часть новых уже на месте
            raise OSError("диск отвалился")
        real_move(source, target)

    monkeypatch.setattr(swap, "_move", flaky_move)
    with pytest.raises(OSError):
        sync(folders)
    monkeypatch.setattr(swap, "_move", real_move)

    assert read_world(folders["target"]) == before
    assert list_backups(folders["backups"], "World") == []
    assert leftovers(folders["tmp"]) == []


def test_interrupted_swap_is_recovered_on_next_start(folders: dict[str, Path]) -> None:
    """Компьютер выключился посреди замены: при запуске мир возвращается по журналу."""
    world = folders["target"]
    make_world(world, {"level.dat": b"old", "keep": b"k"})
    before = read_world(world)
    staging = create_staging_dir(folders["tmp"])
    write_file(staging / "files" / "level.dat", b"new")
    write_file(staging / "files" / "added", b"a")
    backup = folders["backups"] / "World" / "2026-10-21_12-00-00"
    state = {"world": str(world), "new_files": str(staging / "files"), "backup": str(backup),
             "put": ["level.dat", "added"], "remove": [], "existed": {"level.dat": "level.dat"}}
    swap._write_json(staging / swap.JOURNAL_NAME, state)
    # «Выключение» после того, как старый level.dat ушёл в бэкап, а новый уже встал на место.
    swap._move(world / "level.dat", backup / "files" / "level.dat")
    swap._move(staging / "files" / "level.dat", world / "level.dat")

    assert cleanup_leftovers(folders["tmp"]) == 1

    assert read_world(world) == before
    assert not backup.exists()


def test_nfd_names_are_replaced(folders: dict[str, Path]) -> None:
    """Файл с именем в NFD (так бывает после macOS) правильно уходит в бэкап и заменяется."""
    nfd = unicodedata.normalize("NFD", "data/ёжик.dat")
    make_world(folders["target"], {nfd: b"old"})
    make_world(folders["source"], {"data/ёжик.dat": b"new"}, mtime=BASE_TIME + 10)

    sync(folders)

    assert read_world(folders["target"]) == {"data/ёжик.dat": b"new"}
