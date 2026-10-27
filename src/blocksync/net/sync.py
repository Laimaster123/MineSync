"""Подготовка синхронизации: сравнить мир на двух устройствах и проверить, можно ли.

Сначала пользователь отвечает «где играл последним» — это задаёт направление:
  PULL — эталон другое устройство, мир меняется здесь (передачу делаем мы);
  PUSH — эталон это устройство, мир меняется там (просим его забрать мир у нас).
Затем prepare_sync() показывает, что будет сделано, и какие есть предупреждения.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from blocksync.core.checks import CheckResult, check_sync
from blocksync.core.compare import SyncPlan, compare
from blocksync.core.lock import is_world_locked
from blocksync.core.manifest import Manifest, build_manifest
from blocksync.core.world import is_safe_world_name, is_world_dir, read_data_version
from blocksync.net.client import NetworkError, PeerClient


class Direction(Enum):
    PULL = "pull"  # эталон — другое устройство
    PUSH = "push"  # эталон — это устройство


@dataclass(frozen=True)
class WorldSide:
    """Мир на одном из устройств (пустой манифест — мира там нет)."""

    manifest: Manifest
    data_version: int | None
    is_locked: bool


@dataclass(frozen=True)
class SyncPreview:
    """Что будет сделано, и можно ли это делать."""

    world: str
    direction: Direction
    plan: SyncPlan
    check: CheckResult


def local_side(world_dir: Path) -> WorldSide:
    if not is_world_dir(world_dir):
        return WorldSide({}, None, False)
    return WorldSide(build_manifest(world_dir), read_data_version(world_dir), is_world_locked(world_dir))


def remote_side(client: PeerClient, world: str) -> WorldSide:
    if world not in {w.folder_name for w in client.list_worlds()}:
        return WorldSide({}, None, False)
    remote = client.get_manifest(world)
    return WorldSide(remote.manifest, remote.data_version, remote.is_locked)


def prepare_sync(
    client: PeerClient, world: str, direction: Direction, saves_dir: Path, tmp_root: Path,
    full_backup: bool = False,
) -> SyncPreview:
    """Сравнить мир на обоих устройствах и проверить предупреждения и блокировки."""
    if not is_safe_world_name(world):
        raise NetworkError(f"неверное имя мира: {world!r}", "net_bad_world")
    here = local_side(saves_dir / world)
    there = remote_side(client, world)
    if direction is Direction.PULL:
        source, target, receiver_tmp = there, here, tmp_root
    else:
        # Место на диске получателя проверит само другое устройство.
        source, target, receiver_tmp = here, there, None
    plan = compare(source.manifest, target.manifest)
    check = check_sync(
        plan, source.manifest, target.manifest,
        source.data_version, target.data_version,
        source.is_locked, target.is_locked, receiver_tmp,
        full_copy=full_backup or not target.manifest,
    )
    return SyncPreview(world, direction, plan, check)
