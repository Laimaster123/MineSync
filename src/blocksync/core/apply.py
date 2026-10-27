"""Применение плана синхронизации: получатель становится копией эталона.

Два режима:
- быстрый (по умолчанию): во временную папку скачиваются только новые и изменённые
  файлы, затем они переставляются в мир, а старые версии уходят в «бэкап изменений»
  (core/swap.py). Неизменённые файлы не трогаются, диск почти не нагружается;
- полный бэкап (full_backup=True): во временной папке собирается полная новая
  версия мира, старый мир целиком уходит в бэкап, новая версия встаёт на его место.
В обоих режимах при ошибке мир остаётся как был.
"""

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from blocksync.core.backups import new_backup_path, prune_backups, replace_world
from blocksync.core.compare import SyncPlan
from blocksync.core.lock import WorldLockedError, is_world_locked
from blocksync.core.manifest import FileEntry, find_on_disk
from blocksync.core.staging import create_staging_dir, discard_staging_dir
from blocksync.core.swap import JOURNAL_NAME, swap_files

logger = logging.getLogger(__name__)

# Файлы копируются кусками по 1 МБ, чтобы не загружать большие файлы в память целиком.
CHUNK_SIZE = 1024 * 1024

# Функция прогресса: (сколько байт готово, сколько всего).
ProgressCallback = Callable[[int, int], None]


class FileSource(Protocol):
    """Откуда брать файлы эталона.

    Это локальная папка (LocalFolderSource) или другое устройство по сети
    (net/client.RemoteFileSource) — код синхронизации одинаков для обоих.
    """

    def open_file(self, entry: FileEntry) -> BinaryIO: ...


class LocalFolderSource:
    """Источник файлов из локальной папки мира."""

    def __init__(self, world_dir: Path) -> None:
        self.world_dir = world_dir

    def open_file(self, entry: FileEntry) -> BinaryIO:
        return open(find_on_disk(self.world_dir, entry.path), "rb")


class SyncError(Exception):
    """Синхронизация не удалась, мир оставлен как был.

    code и params — для текста на языке интерфейса (как у net.client.NetworkError).
    """

    def __init__(self, message: str, code: str | None = None, **params: object) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


class SyncCancelled(Exception):
    """Синхронизация отменена пользователем, мир оставлен как был."""


@dataclass(frozen=True)
class SyncResult:
    copied_files: int
    deleted_files: int
    bytes_copied: int
    backup_path: Path | None  # где лежит прежняя версия мира
    duration_seconds: float
    bytes_transferred: int | None = None  # сколько пришло по сети (меньше bytes_copied, если сжато)


def apply_sync(
    plan: SyncPlan,
    source: FileSource,
    world_dir: Path,
    tmp_root: Path,
    backups_root: Path,
    keep_backups: int,
    progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    full_backup: bool = False,
    before_replace: Callable[[], None] | None = None,
) -> SyncResult:
    """Сделать мир получателя (world_dir) копией эталона по готовому плану.

    before_replace вызывается, когда все файлы скачаны, но мир ещё не тронут:
    последняя возможность отказаться (например, если эталон изменился за время передачи).
    """
    started = time.monotonic()
    if not plan.has_changes:
        return SyncResult(0, 0, 0, None, 0.0)
    if is_world_locked(world_dir):
        raise WorldLockedError(world_dir)

    # Мира ещё нет — скачивать придётся всё, бэкап не нужен: собираем мир целиком.
    full = full_backup or not world_dir.exists()
    copier = _Copier(plan, progress, should_cancel, include_unchanged=full)
    staging = create_staging_dir(tmp_root)
    try:
        if full:
            local = LocalFolderSource(world_dir)
            for entry in plan.unchanged:
                copier.copy(local, entry, staging)
            for entry in plan.to_copy:
                copier.copy(source, entry, staging)
            if before_replace is not None:
                before_replace()
            # Пока файлы копировались, мир могли открыть в игре.
            if is_world_locked(world_dir):
                raise WorldLockedError(world_dir)
            backup_path = replace_world(staging, world_dir, backups_root, keep_backups)
        else:
            new_files = staging / "files"
            for entry in plan.to_copy:
                copier.copy(source, entry, new_files)
            if before_replace is not None:
                before_replace()
            # Пока файлы скачивались, мир могли открыть в игре.
            if is_world_locked(world_dir):
                raise WorldLockedError(world_dir)
            backup_path = new_backup_path(backups_root / world_dir.name)
            swap_files(
                world_dir, new_files, [e.path for e in plan.to_copy], [e.path for e in plan.to_delete],
                backup_path, staging / JOURNAL_NAME,
            )
            prune_backups(backups_root, world_dir.name, keep_backups)
    except BaseException:
        # BaseException ловит и отмену, и Ctrl+C: временную папку убираем в любом случае.
        discard_staging_dir(staging)
        raise
    discard_staging_dir(staging)

    result = SyncResult(
        copied_files=len(plan.to_copy),
        deleted_files=len(plan.to_delete),
        bytes_copied=plan.bytes_to_copy,
        backup_path=backup_path,
        duration_seconds=time.monotonic() - started,
    )
    logger.info("Мир %s синхронизирован: %s", world_dir.name, result)
    return result


class _Copier:
    """Копирует файлы во временную папку и считает общий прогресс."""

    def __init__(
        self,
        plan: SyncPlan,
        progress: ProgressCallback | None,
        should_cancel: Callable[[], bool] | None,
        include_unchanged: bool = True,
    ) -> None:
        self.total = plan.bytes_to_copy + (sum(entry.size for entry in plan.unchanged) if include_unchanged else 0)
        self.done = 0
        self.progress = progress
        self.should_cancel = should_cancel

    def copy(self, source: FileSource, entry: FileEntry, staging: Path) -> None:
        destination = staging / entry.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with source.open_file(entry) as reader, open(destination, "wb") as writer:
            # Читаем по куску, пока read() не вернёт пустые байты (конец файла).
            while chunk := reader.read(CHUNK_SIZE):
                if self.should_cancel is not None and self.should_cancel():
                    raise SyncCancelled()
                writer.write(chunk)
                written += len(chunk)
                self.done += len(chunk)
                if self.progress is not None:
                    self.progress(self.done, self.total)
        if written != entry.size:
            raise SyncError(
                f"размер файла {entry.path} не совпал: ожидалось {entry.size}, получено {written}",
                "sync_size_mismatch", path=entry.path,
            )
        # Время изменения как у эталона, чтобы следующее сравнение не нашло лишних различий.
        os.utime(destination, ns=(entry.mtime_ns, entry.mtime_ns))
