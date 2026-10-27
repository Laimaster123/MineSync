"""Состояние мира после последней синхронизации и подсказка «где играли последним»."""

from datetime import datetime
from pathlib import Path

from blocksync.config.sync_state import SyncState, SyncStateStore, newest
from blocksync.core.manifest import FileEntry, Manifest
from blocksync.net.sync import Basis, Direction, suggest_direction

T = 1_700_000_000 * 10**9  # время файлов, нс


def manifest(**files: tuple[int, int]) -> Manifest:
    """manifest(a=(размер, сдвиг времени в секундах))"""
    return {name: FileEntry(name, size, T + shift * 10**9) for name, (size, shift) in files.items()}


def state(m: Manifest) -> SyncState:
    return SyncState(datetime(2026, 10, 20, 18, 0), m)


def test_store_round_trip_and_missing(tmp_path: Path) -> None:
    store = SyncStateStore(tmp_path / "sync_state")
    synced = manifest(a=(1, 0), **{"region/r.0.0.mca": (4096, 5)})
    assert store.load("prism", "Мир/с именем") is None

    store.save("prism", "Мир/с именем", synced, datetime(2026, 10, 20, 18, 0, 5, 123))

    loaded = store.load("prism", "Мир/с именем")
    assert loaded == SyncState(datetime(2026, 10, 20, 18, 0, 5), synced)
    assert store.load(None, "Мир/с именем") is None  # другая сборка — другое состояние


def test_damaged_state_is_ignored(tmp_path: Path) -> None:
    store = SyncStateStore(tmp_path)
    store.save(None, "Мир", manifest(a=(1, 0)))
    next(tmp_path.iterdir()).write_bytes(b"not gzip")
    assert store.load(None, "Мир") is None


def test_newest_state() -> None:
    old, new = state({}), SyncState(datetime(2026, 10, 21), {})
    assert newest(None, old, new) is new
    assert newest(None, None) is None


def test_only_here_changed_suggests_push() -> None:
    synced = manifest(a=(1, 0), b=(1, 0))
    suggestion = suggest_direction(manifest(a=(2, -100), b=(1, 0)), synced, state(synced))
    # Часы Мака отстают (файл «старше»), но по прошлой синхронизации видно, где играли.
    assert suggestion.direction is Direction.PUSH and suggestion.basis is Basis.LAST_SYNC


def test_only_there_changed_suggests_pull() -> None:
    synced = manifest(a=(1, 0))
    suggestion = suggest_direction(synced, manifest(a=(1, 300)), state(synced))
    assert suggestion.direction is Direction.PULL and not suggestion.conflict


def test_both_changed_is_conflict() -> None:
    synced = manifest(a=(1, 0), b=(1, 0))
    suggestion = suggest_direction(manifest(a=(2, 10), b=(1, 0)), manifest(a=(1, 0), b=(3, 20)), state(synced))
    assert suggestion.conflict and suggestion.direction is None and suggestion.last_sync is not None


def test_without_last_sync_newer_files_win() -> None:
    suggestion = suggest_direction(manifest(a=(1, 0)), manifest(a=(2, 600)), None)
    assert suggestion.direction is Direction.PULL and suggestion.basis is Basis.NEWER_FILES
    # Разница меньше допуска — подсказать нечего.
    assert suggest_direction(manifest(a=(1, 0)), manifest(a=(2, 1)), None).direction is None


def test_world_on_one_side_and_identical_worlds() -> None:
    assert suggest_direction(manifest(a=(1, 0)), {}, None).direction is Direction.PUSH
    assert suggest_direction({}, manifest(a=(1, 0)), None).direction is Direction.PULL
    assert suggest_direction(manifest(a=(1, 0)), manifest(a=(1, 1)), None).direction is None
