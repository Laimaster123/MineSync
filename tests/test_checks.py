from collections import namedtuple
from pathlib import Path

import pytest

from blocksync.core import checks
from blocksync.core.checks import SyncBlocker, SyncWarning, check_sync
from blocksync.core.compare import compare
from blocksync.core.manifest import FileEntry, Manifest

SECOND_NS = 1_000_000_000


def manifest(*entries: FileEntry) -> Manifest:
    return {entry.path: entry for entry in entries}


def run_check(
    source: Manifest,
    target: Manifest,
    tmp_path: Path,
    source_version: int | None = 100,
    target_version: int | None = 100,
    source_locked: bool = False,
    target_locked: bool = False,
) -> checks.CheckResult:
    return check_sync(
        compare(source, target), source, target,
        source_version, target_version, source_locked, target_locked,
        tmp_path / "app" / "tmp",
    )


SOURCE = manifest(FileEntry("level.dat", 10, 500 * SECOND_NS), FileEntry("a", 10, 500 * SECOND_NS))


def test_normal_sync_has_no_warnings(tmp_path: Path) -> None:
    target = manifest(FileEntry("level.dat", 10, 100 * SECOND_NS))

    result = run_check(SOURCE, target, tmp_path)

    assert result.warnings == [] and result.blockers == []
    assert result.can_sync


# 12
def test_receiver_newer_warning(tmp_path: Path) -> None:
    target = manifest(FileEntry("level.dat", 10, 900 * SECOND_NS))
    assert SyncWarning.RECEIVER_NEWER in run_check(SOURCE, target, tmp_path).warnings


def test_receiver_newer_within_tolerance_is_ignored(tmp_path: Path) -> None:
    target = manifest(FileEntry("level.dat", 10, 501 * SECOND_NS))
    assert SyncWarning.RECEIVER_NEWER not in run_check(SOURCE, target, tmp_path).warnings


# 13
def test_downgrade_warning(tmp_path: Path) -> None:
    target = manifest(FileEntry("level.dat", 10, 100 * SECOND_NS))
    result = run_check(SOURCE, target, tmp_path, source_version=4000, target_version=5023)
    assert SyncWarning.DOWNGRADE in result.warnings


def test_unknown_version_gives_no_downgrade_warning(tmp_path: Path) -> None:
    result = run_check(SOURCE, {}, tmp_path, source_version=None, target_version=5023)
    assert SyncWarning.DOWNGRADE not in result.warnings


def test_mass_delete_warning(tmp_path: Path) -> None:
    target = manifest(
        FileEntry("level.dat", 10, 100 * SECOND_NS),
        *(FileEntry(f"old{i}", 1, 100 * SECOND_NS) for i in range(3)),
    )
    # Удаляется 3 файла из 4 — больше 30%.
    assert SyncWarning.MASS_DELETE in run_check(SOURCE, target, tmp_path).warnings


# 7
@pytest.mark.parametrize("source_locked, target_locked", [(True, False), (False, True)])
def test_locked_world_blocks_sync(tmp_path: Path, source_locked: bool, target_locked: bool) -> None:
    result = run_check(SOURCE, {}, tmp_path, source_locked=source_locked, target_locked=target_locked)
    assert SyncBlocker.WORLD_LOCKED in result.blockers
    assert not result.can_sync


# 14
def test_not_enough_space_blocks_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    DiskUsage = namedtuple("DiskUsage", "total used free")
    monkeypatch.setattr(checks.shutil, "disk_usage", lambda path: DiskUsage(100, 90, 10))

    result = run_check(SOURCE, {}, tmp_path)

    assert SyncBlocker.NOT_ENOUGH_SPACE in result.blockers
    assert result.free_bytes == 10
    assert result.required_bytes == 20 + checks.FREE_SPACE_MARGIN_BYTES


def test_no_changes_needs_no_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    DiskUsage = namedtuple("DiskUsage", "total used free")
    monkeypatch.setattr(checks.shutil, "disk_usage", lambda path: DiskUsage(100, 100, 0))

    assert run_check(SOURCE, SOURCE, tmp_path).can_sync
