"""Забрать мир с другого устройства: манифест -> сравнение -> проверки -> применение."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from blocksync.core.apply import ProgressCallback, SyncResult, apply_sync
from blocksync.core.checks import NotEnoughSpaceError, SyncBlocker, check_sync
from blocksync.core.compare import compare
from blocksync.core.lock import WorldLockedError, is_world_locked
from blocksync.core.manifest import build_manifest
from blocksync.core.world import is_safe_world_name, read_data_version
from blocksync.net.client import NetworkError, PeerClient, RemoteFileSource, SourceChangedError


def pull_world(
    client: PeerClient,
    world: str,
    saves_dir: Path,
    tmp_root: Path,
    backups_root: Path,
    keep_backups: int,
    progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    full_backup: bool = False,
) -> SyncResult:
    """Сделать локальный мир копией мира на другом устройстве.

    Предупреждения показывает интерфейс до вызова этой функции (см. net/sync.py).
    Здесь ещё раз проверяется то, что могло измениться: мир не открыт в игре
    и на диске хватает места.
    """
    if not is_safe_world_name(world):
        raise NetworkError(f"неверное имя мира: {world!r}", "net_bad_world")
    remote = client.get_manifest(world)
    if remote.is_locked:
        raise WorldLockedError(Path(world))
    world_dir = saves_dir / world
    local = build_manifest(world_dir) if world_dir.exists() else {}
    plan = compare(remote.manifest, local)
    check = check_sync(
        plan, remote.manifest, local,
        remote.data_version, read_data_version(world_dir),
        remote.is_locked, is_world_locked(world_dir), tmp_root,
        full_copy=full_backup or not world_dir.exists(),
    )
    if SyncBlocker.NOT_ENOUGH_SPACE in check.blockers:
        raise NotEnoughSpaceError(check.required_bytes, check.free_bytes)
    source = RemoteFileSource(client, world, should_cancel)

    def check_source_unchanged() -> None:
        """Пока файлы шли по сети, мир на том устройстве могли открыть в игре или изменить.
        Тогда скачанные файлы — из разных моментов, ставить их в мир нельзя."""
        fresh = client.get_manifest(world)
        if fresh.is_locked:
            raise WorldLockedError(Path(world))
        if compare(fresh.manifest, remote.manifest).has_changes:
            raise SourceChangedError("список файлов изменился")

    result = apply_sync(
        plan, source, world_dir, tmp_root, backups_root, keep_backups, progress, should_cancel, full_backup,
        before_replace=check_source_unchanged,
    )
    return replace(result, bytes_transferred=source.wire_bytes) if result.copied_files else result
