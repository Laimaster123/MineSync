"""Подготовка синхронизации: сравнить мир на двух устройствах и проверить, можно ли.

Сначала пользователь отвечает «где играл последним» — это задаёт направление:
  PULL — эталон другое устройство, мир меняется здесь (передачу делаем мы);
  PUSH — эталон это устройство, мир меняется там (просим его забрать мир у нас).
Подсказку для ответа даёт suggest_direction(): по состоянию мира после прошлой
синхронизации видно, где он изменился с тех пор (config/sync_state.py).
Затем prepare_sync() показывает, что будет сделано, и какие есть предупреждения.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from blocksync.config.sync_state import SyncState, newest
from blocksync.core.checks import CheckResult, SyncWarning, check_sync
from blocksync.core.compare import MTIME_TOLERANCE_NS, SyncPlan, compare
from blocksync.core.lock import is_world_locked
from blocksync.core.manifest import Manifest, build_manifest, newest_mtime_ns
from blocksync.core.world import is_safe_world_name, is_world_dir, read_data_version
from blocksync.net.client import NetworkError, PeerClient


class Direction(Enum):
    PULL = "pull"  # эталон — другое устройство
    PUSH = "push"  # эталон — это устройство


class Basis(Enum):
    """На чём основана подсказка направления."""

    LAST_SYNC = "last_sync"  # сравнение с состоянием после прошлой синхронизации — надёжно
    NEWER_FILES = "newer_files"  # прошлой синхронизации не знаем: где файлы новее (зависит от часов)


@dataclass(frozen=True)
class Suggestion:
    """Подсказка «где играли последним».

    direction — что выбрать (None — подсказать нечего или конфликт);
    conflict — мир изменился на обоих устройствах после прошлой синхронизации.
    """

    direction: Direction | None
    basis: Basis | None = None
    conflict: bool = False
    last_sync: SyncState | None = None


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
    here: Manifest  # мир на этом устройстве (пустой — мира нет)


def local_side(world_dir: Path) -> WorldSide:
    if not is_world_dir(world_dir):
        return WorldSide({}, None, False)
    return WorldSide(build_manifest(world_dir), read_data_version(world_dir), is_world_locked(world_dir))


def remote_side(client: PeerClient, world: str) -> WorldSide:
    if world not in {w.folder_name for w in client.list_worlds()}:
        return WorldSide({}, None, False)
    remote = client.get_manifest(world)
    return WorldSide(remote.manifest, remote.data_version, remote.is_locked)


def last_sync_state(client: PeerClient, world: str, own: SyncState | None) -> SyncState | None:
    """Состояние мира после последней синхронизации: своё или того устройства, что свежее."""
    try:
        theirs = client.get_sync_state(world)
    except NetworkError:
        theirs = None  # не страшно: подсказка будет менее точной
    return newest(own, theirs)


def suggest_direction(here: Manifest, there: Manifest, last_sync: SyncState | None) -> Suggestion:
    """Где, скорее всего, играли последним."""
    if not here or not there:
        # Мир есть только на одном устройстве — переносить можно только оттуда.
        if here or there:
            return Suggestion(Direction.PUSH if here else Direction.PULL)
        return Suggestion(None)
    if not compare(there, here).has_changes:
        return Suggestion(None)  # миры одинаковы
    if last_sync is not None:
        here_changed = compare(here, last_sync.manifest).has_changes
        there_changed = compare(there, last_sync.manifest).has_changes
        if here_changed and there_changed:
            return Suggestion(None, Basis.LAST_SYNC, conflict=True, last_sync=last_sync)
        if here_changed != there_changed:
            return Suggestion(Direction.PUSH if here_changed else Direction.PULL, Basis.LAST_SYNC, last_sync=last_sync)
    here_newest, there_newest = newest_mtime_ns(here) or 0, newest_mtime_ns(there) or 0
    if abs(here_newest - there_newest) <= MTIME_TOLERANCE_NS:
        return Suggestion(None)
    return Suggestion(Direction.PUSH if here_newest > there_newest else Direction.PULL, Basis.NEWER_FILES)


def prepare_sync(
    client: PeerClient, world: str, direction: Direction, saves_dir: Path, tmp_root: Path,
    full_backup: bool = False, last_sync: SyncState | None = None,
) -> SyncPreview:
    """Сравнить мир на обоих устройствах и проверить предупреждения и блокировки.

    last_sync — состояние после прошлой синхронизации: если оно известно, вместо
    догадки «у получателя файлы новее» точно видно, менялся ли мир у получателя.
    """
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
    if last_sync is not None and source.manifest and target.manifest and plan.has_changes:
        _apply_last_sync(check, source.manifest, target.manifest, last_sync)
    return SyncPreview(world, direction, plan, check, here.manifest)


def _apply_last_sync(check: CheckResult, source: Manifest, target: Manifest, last_sync: SyncState) -> None:
    """Уточнить предупреждения по состоянию после прошлой синхронизации."""
    if SyncWarning.RECEIVER_NEWER in check.warnings:
        # Вместо догадки по времени файлов (она зависит от часов компьютеров) — точный ответ ниже.
        check.warnings.remove(SyncWarning.RECEIVER_NEWER)
    if not compare(target, last_sync.manifest).has_changes:
        return  # получатель с прошлой синхронизации не менялся: терять нечего
    source_changed = compare(source, last_sync.manifest).has_changes
    check.warnings.insert(0, SyncWarning.BOTH_CHANGED if source_changed else SyncWarning.RECEIVER_CHANGED)
