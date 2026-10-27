"""Проверки перед синхронизацией.

Предупреждения можно пропустить после подтверждения пользователя.
Блокировки запрещают синхронизацию совсем.
Тексты сообщений здесь не хранятся: их подберёт интерфейс по виду проверки.
"""

import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from blocksync.core.compare import MTIME_TOLERANCE_NS, SyncPlan
from blocksync.core.manifest import Manifest, newest_mtime_ns, total_size

# Если будет удалено больше этой доли файлов получателя — предупреждаем.
MASS_DELETE_THRESHOLD = 0.30

# Запас свободного места сверх размера мира: 50 МБ.
FREE_SPACE_MARGIN_BYTES = 50 * 1024 * 1024


class SyncWarning(Enum):
    RECEIVER_NEWER = "receiver_newer"  # мир у получателя изменён позже, чем у эталона
    DOWNGRADE = "downgrade"  # у получателя мир более новой версии игры
    MASS_DELETE = "mass_delete"  # удаляется слишком много файлов
    # Ниже — по состоянию после прошлой синхронизации (config/sync_state.py):
    BOTH_CHANGED = "both_changed"  # мир изменился на обоих устройствах: изменения получателя пропадут
    RECEIVER_CHANGED = "receiver_changed"  # мир менялся только у получателя: похоже, направление выбрано не то


class SyncBlocker(Enum):
    WORLD_LOCKED = "world_locked"  # мир открыт в игре на одном из устройств
    NOT_ENOUGH_SPACE = "not_enough_space"  # на диске получателя не хватает места
    SOURCE_MISSING = "source_missing"  # у эталона нет такого мира: получатель потерял бы его целиком


class NotEnoughSpaceError(Exception):
    """На диске получателя не хватает места для синхронизации."""

    def __init__(self, required_bytes: int, free_bytes: int) -> None:
        super().__init__(f"не хватает места: нужно {required_bytes} байт, свободно {free_bytes}")
        self.required_bytes = required_bytes
        self.free_bytes = free_bytes


@dataclass
class CheckResult:
    warnings: list[SyncWarning] = field(default_factory=list)
    blockers: list[SyncBlocker] = field(default_factory=list)
    required_bytes: int = 0
    free_bytes: int = 0

    @property
    def can_sync(self) -> bool:
        return not self.blockers


def check_sync(
    plan: SyncPlan,
    source: Manifest,
    target: Manifest,
    source_data_version: int | None,
    target_data_version: int | None,
    source_locked: bool,
    target_locked: bool,
    tmp_root: Path | None,
    full_copy: bool = True,
) -> CheckResult:
    """Проверить, можно ли синхронизировать, и о чём предупредить пользователя.

    tmp_root — где получатель собирает новую версию мира. None — получатель
    другое устройство: место на его диске проверит он сам перед началом.
    full_copy — собирается полная копия мира (режим «полный бэкап» или мира ещё нет);
    иначе место нужно только под новые и изменённые файлы.
    """
    result = CheckResult()

    source_newest = newest_mtime_ns(source)
    target_newest = newest_mtime_ns(target)
    if (
        source_newest is not None
        and target_newest is not None
        and target_newest > source_newest + MTIME_TOLERANCE_NS
    ):
        result.warnings.append(SyncWarning.RECEIVER_NEWER)

    if (
        source_data_version is not None
        and target_data_version is not None
        and target_data_version > source_data_version
    ):
        result.warnings.append(SyncWarning.DOWNGRADE)

    if target and len(plan.to_delete) / len(target) > MASS_DELETE_THRESHOLD:
        result.warnings.append(SyncWarning.MASS_DELETE)

    if source_locked or target_locked:
        result.blockers.append(SyncBlocker.WORLD_LOCKED)

    if not source:
        result.blockers.append(SyncBlocker.SOURCE_MISSING)

    if plan.has_changes and tmp_root is not None:
        needed = total_size(source) if full_copy else plan.bytes_to_copy
        result.required_bytes = needed + FREE_SPACE_MARGIN_BYTES
        result.free_bytes = free_space(tmp_root)
        if result.free_bytes < result.required_bytes:
            result.blockers.append(SyncBlocker.NOT_ENOUGH_SPACE)

    return result


def free_space(path: Path) -> int:
    """Свободное место на диске, где находится path (папка может ещё не существовать)."""
    existing = path
    while not existing.exists():
        existing = existing.parent
    return shutil.disk_usage(existing).free
