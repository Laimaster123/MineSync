"""Сценарии синхронизации.

Два «устройства» имитируются двумя папками: source (эталон) и target (получатель).
"""

import io
import unicodedata
from pathlib import Path
from typing import BinaryIO

import pytest

from blocksync.core.apply import FileSource, LocalFolderSource, SyncCancelled, SyncError, SyncResult, apply_sync
from blocksync.core.backups import Backup, list_backups, restore_backup
from blocksync.core.compare import SyncPlan, compare
from blocksync.core.lock import WorldLockedError
from blocksync.core.manifest import FileEntry, build_manifest

from helpers import BASE_TIME, hold_lock, leftovers, make_world, read_world, write_file

WORLD_FILES = {
    "level.dat": b"level",
    "region/r.0.0.mca": b"region-00" * 100,
    "region/r.0.1.mca": b"region-01" * 100,
    "data/raids.dat": b"raids",
}


class Env:
    """Папки тестового окружения."""

    def __init__(self, tmp_path: Path, full_backup: bool = False) -> None:
        self.full_backup = full_backup
        self.source = tmp_path / "pc" / "saves" / "World"
        self.target = tmp_path / "mac" / "saves" / "World"
        self.tmp = tmp_path / "mac" / "app" / "tmp"
        self.backups = tmp_path / "mac" / "app" / "backups"

    def plan(self) -> SyncPlan:
        target = build_manifest(self.target) if self.target.exists() else {}
        return compare(build_manifest(self.source), target)

    def sync(self, source: FileSource | None = None) -> SyncResult:
        file_source = source if source is not None else LocalFolderSource(self.source)
        return apply_sync(
            self.plan(), file_source, self.target, self.tmp, self.backups, keep_backups=3,
            full_backup=self.full_backup,
        )


# Каждый сценарий проверяется в обоих режимах: быстром и с полным бэкапом мира.
@pytest.fixture(params=[False, True], ids=["fast", "full"])
def env(tmp_path: Path, request: pytest.FixtureRequest) -> Env:
    return Env(tmp_path, full_backup=request.param)


def backup_content(backup: Backup) -> dict[str, bytes]:
    """Что лежит в бэкапе: весь мир (полный бэкап) или старые версии файлов (бэкап изменений)."""
    return read_world(backup.path / "files") if backup.is_delta else read_world(backup.path)


def assert_same_worlds(env: Env) -> None:
    assert read_world(env.target) == read_world(env.source)
    assert not env.plan().has_changes  # повторное сравнение не находит различий


# 1
def test_missing_world_is_created(env: Env) -> None:
    make_world(env.source, WORLD_FILES)

    result = env.sync()

    assert_same_worlds(env)
    assert result.copied_files == 4
    assert result.backup_path is None


# 2
def test_unchanged_world_copies_nothing(env: Env) -> None:
    make_world(env.source, WORLD_FILES)
    make_world(env.target, WORLD_FILES)

    result = env.sync()

    assert result.copied_files == 0 and result.deleted_files == 0
    assert list_backups(env.backups, "World") == []


# 3
def test_only_changed_region_file_is_copied(env: Env) -> None:
    make_world(env.source, WORLD_FILES)
    make_world(env.target, WORLD_FILES)
    write_file(env.source / "region/r.0.0.mca", b"changed", mtime=BASE_TIME + 100)

    plan = env.plan()
    result = env.sync()

    assert [e.path for e in plan.to_copy] == ["region/r.0.0.mca"]
    assert result.bytes_copied == len(b"changed")
    assert_same_worlds(env)


# 4
def test_file_deleted_at_source_is_deleted_at_target(env: Env) -> None:
    make_world(env.source, WORLD_FILES)
    make_world(env.target, WORLD_FILES)
    (env.source / "data/raids.dat").unlink()

    env.sync()

    assert not (env.target / "data/raids.dat").exists()
    assert_same_worlds(env)


# 5
def test_extra_file_at_target_is_deleted(env: Env) -> None:
    make_world(env.source, WORLD_FILES)
    make_world(env.target, {**WORLD_FILES, "region/r.9.9.mca": b"extra"})

    result = env.sync()

    assert result.deleted_files == 1
    assert_same_worlds(env)


# 6
def test_one_second_difference_is_not_a_change(env: Env) -> None:
    make_world(env.source, WORLD_FILES, mtime=BASE_TIME)
    make_world(env.target, WORLD_FILES, mtime=BASE_TIME + 1)

    assert not env.plan().has_changes


# 7
def test_locked_target_world_blocks_sync(env: Env) -> None:
    make_world(env.source, WORLD_FILES)
    make_world(env.target, {"level.dat": b"old", "session.lock": b"lock"})

    with hold_lock(env.target / "session.lock"):
        with pytest.raises(WorldLockedError):
            env.sync()

    assert read_world(env.target) == {"level.dat": b"old", "session.lock": b"lock"}


class BrokenSource:
    """Источник, у которого «обрывается связь» посреди передачи файла."""

    def __init__(self, world_dir: Path) -> None:
        self.world_dir = world_dir

    def open_file(self, entry: FileEntry) -> BinaryIO:
        return _BrokenReader((self.world_dir / entry.path).read_bytes())


class _BrokenReader(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        if self.tell() > 0:
            raise ConnectionError("связь оборвалась")
        return super().read(5)


# 8
def test_broken_transfer_leaves_world_untouched(env: Env) -> None:
    make_world(env.source, WORLD_FILES)
    make_world(env.target, WORLD_FILES)
    write_file(env.source / "region/r.0.0.mca", b"new content", mtime=BASE_TIME + 100)
    before = read_world(env.target)

    with pytest.raises(ConnectionError):
        env.sync(BrokenSource(env.source))

    assert read_world(env.target) == before
    assert leftovers(env.tmp) == []
    assert list_backups(env.backups, "World") == []


def test_size_mismatch_is_an_error(env: Env) -> None:
    make_world(env.source, WORLD_FILES)
    plan = env.plan()
    # Файл изменился у эталона уже после построения плана.
    write_file(env.source / "level.dat", b"longer level data")

    with pytest.raises(SyncError):
        apply_sync(plan, LocalFolderSource(env.source), env.target, env.tmp, env.backups, 3)

    assert not env.target.exists()
    assert leftovers(env.tmp) == []


def test_cancel_leaves_world_untouched(env: Env) -> None:
    make_world(env.source, WORLD_FILES)
    make_world(env.target, {"level.dat": b"old"})

    with pytest.raises(SyncCancelled):
        apply_sync(
            env.plan(), LocalFolderSource(env.source), env.target, env.tmp, env.backups, 3,
            should_cancel=lambda: True,
        )

    assert read_world(env.target) == {"level.dat": b"old"}
    assert leftovers(env.tmp) == []


def test_progress_reaches_total(env: Env) -> None:
    make_world(env.source, WORLD_FILES)
    make_world(env.target, {"level.dat": b"old"})
    calls: list[tuple[int, int]] = []

    apply_sync(
        env.plan(), LocalFolderSource(env.source), env.target, env.tmp, env.backups, 3,
        progress=lambda done, total: calls.append((done, total)),
    )

    done, total = calls[-1]
    assert done == total == sum(len(c) for c in WORLD_FILES.values())


# 9
def test_backup_before_replace_and_rotation(env: Env) -> None:
    make_world(env.target, {"level.dat": b"v0"})
    for version in range(1, 5):
        make_world(env.source, {"level.dat": f"v{version}".encode()}, mtime=BASE_TIME + version * 10)
        env.sync()

    backups = list_backups(env.backups, "World")
    assert [backup_content(b)["level.dat"] for b in backups] == [b"v3", b"v2", b"v1"]


# 10
def test_restore_from_backup(env: Env) -> None:
    make_world(env.target, {"level.dat": b"before", "region/a.mca": b"a"})
    make_world(env.source, {"level.dat": b"after"}, mtime=BASE_TIME + 10)
    result = env.sync()
    assert result.backup_path is not None
    backup = list_backups(env.backups, "World")[0]

    restore_backup(backup, env.target, env.tmp, env.backups, keep=3)

    assert read_world(env.target) == {"level.dat": b"before", "region/a.mca": b"a"}


# 11
def test_cyrillic_and_spaces_in_names(env: Env) -> None:
    files = {
        "level.dat": b"x",
        "data/мой файл (копия).dat": b"cyr",
        unicodedata.normalize("NFD", "data/ёжик и йод.dat"): b"nfd",
    }
    make_world(env.source, files)
    make_world(env.target, {"level.dat": b"x"})

    env.sync()

    assert not env.plan().has_changes
    names = {unicodedata.normalize("NFC", name) for name in read_world(env.target)}
    assert unicodedata.normalize("NFC", "data/ёжик и йод.dat") in names
    assert "data/мой файл (копия).dat" in names
