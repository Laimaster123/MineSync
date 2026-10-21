"""Бэкапы миров: создание при замене мира, список, восстановление, удаление старых.

Бэкапы лежат в папке <backups_root>/<имя папки мира>/<дата-время>/.
"""

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from blocksync.core.lock import WorldLockedError, is_world_locked
from blocksync.core.staging import create_staging_dir
from blocksync.core.swap import BACKUP_FILES, DELTA_NAME, JOURNAL_NAME, read_delta, swap_files

logger = logging.getLogger(__name__)

# Сколько бэкапов хранить на один мир: выбирает пользователь.
MIN_BACKUPS = 2
MAX_BACKUPS = 10
DEFAULT_BACKUPS = 3

# Имя папки бэкапа, например 2026-10-19_22-05-30.
TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
_TIMESTAMP_LENGTH = 19


@dataclass(frozen=True)
class Backup:
    world_name: str
    path: Path
    created: datetime
    number: int = 1  # если два бэкапа сделаны в одну секунду, у второго будет 2

    @property
    def is_delta(self) -> bool:
        """«Бэкап изменений»: только старые версии файлов, которые заменила синхронизация."""
        return (self.path / DELTA_NAME).is_file()


def validate_keep(keep: int) -> None:
    """Проверить, что число хранимых бэкапов в допустимых пределах."""
    if not MIN_BACKUPS <= keep <= MAX_BACKUPS:
        raise ValueError(f"число бэкапов должно быть от {MIN_BACKUPS} до {MAX_BACKUPS}, а не {keep}")


def list_backups(backups_root: Path, world_name: str) -> list[Backup]:
    """Бэкапы одного мира, сначала самые новые."""
    world_backups = backups_root / world_name
    if not world_backups.is_dir():
        return []
    backups: list[Backup] = []
    for child in world_backups.iterdir():
        parsed = _parse_backup_name(child.name)
        if child.is_dir() and parsed is not None:
            created, number = parsed
            backups.append(Backup(world_name, child, created, number))
    backups.sort(key=lambda b: (b.created, b.number), reverse=True)
    return backups


def delete_backup(backup: Backup) -> None:
    shutil.rmtree(backup.path)
    logger.info("Удалён бэкап %s", backup.path)


def prune_backups(backups_root: Path, world_name: str, keep: int) -> list[Backup]:
    """Удалить самые старые бэкапы мира, оставив keep штук. Вернуть удалённые."""
    validate_keep(keep)
    old = list_backups(backups_root, world_name)[keep:]
    for backup in old:
        delete_backup(backup)
    return old


def replace_world(prepared_dir: Path, world_dir: Path, backups_root: Path, keep: int) -> Path | None:
    """Поставить подготовленную папку на место мира, а старый мир убрать в бэкап.

    Возвращает путь к бэкапу или None, если мира раньше не было.
    Внутри одного диска оба шага — мгновенные переименования.
    """
    validate_keep(keep)
    backup_path: Path | None = None
    if world_dir.exists():
        backup_path = new_backup_path(backups_root / world_dir.name)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(world_dir, backup_path)
        logger.info("Мир %s перенесён в бэкап %s", world_dir.name, backup_path)
    try:
        shutil.move(prepared_dir, world_dir)
    except BaseException:
        # Не получилось поставить новую версию — возвращаем старую на место.
        if backup_path is not None and not world_dir.exists():
            shutil.move(backup_path, world_dir)
        raise
    prune_backups(backups_root, world_dir.name, keep)
    return backup_path


def restore_backup(
    backup: Backup, world_dir: Path, tmp_root: Path, backups_root: Path, keep: int
) -> Path | None:
    """Восстановить мир из бэкапа.

    Текущая версия мира сама сохраняется в новый бэкап, поэтому восстановление
    тоже можно отменить. Возвращает путь к этому новому бэкапу.
    """
    if is_world_locked(world_dir):
        raise WorldLockedError(world_dir)
    delta = read_delta(backup.path)
    if delta is not None:
        return _restore_delta(backup, delta, world_dir, tmp_root, backups_root, keep)
    staging = create_staging_dir(tmp_root)
    try:
        # Копируем, а не переносим: выбранный бэкап должен остаться.
        shutil.copytree(backup.path, staging, dirs_exist_ok=True)
        return replace_world(staging, world_dir, backups_root, keep)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _restore_delta(
    backup: Backup, delta: dict[str, list[str]], world_dir: Path, tmp_root: Path, backups_root: Path, keep: int
) -> Path:
    """Откатить синхронизацию «бэкапом изменений»: вернуть старые версии файлов, убрать добавленные.

    То, что сейчас лежит на месте этих файлов, само уходит в новый бэкап изменений.
    """
    validate_keep(keep)
    staging = create_staging_dir(tmp_root)
    try:
        files = staging / BACKUP_FILES
        # Копируем (файлов немного): выбранный бэкап должен остаться.
        if (backup.path / BACKUP_FILES).is_dir():
            shutil.copytree(backup.path / BACKUP_FILES, files)
        new_backup = new_backup_path(backups_root / world_dir.name)
        swap_files(world_dir, files, delta["restore_put"], delta["restore_remove"], new_backup, staging / JOURNAL_NAME)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    prune_backups(backups_root, world_dir.name, keep)
    logger.info("Мир %s восстановлен из бэкапа изменений %s", world_dir, backup.path)
    return new_backup


def new_backup_path(world_backups: Path) -> Path:
    """Путь для нового бэкапа по текущему времени, не совпадающий с существующими."""
    base = datetime.now().strftime(TIMESTAMP_FORMAT)
    path = world_backups / base
    number = 2
    while path.exists():
        path = world_backups / f"{base}_{number}"
        number += 1
    return path


def _parse_backup_name(name: str) -> tuple[datetime, int] | None:
    """Разобрать имя папки бэкапа. Для чужих папок вернуть None."""
    try:
        created = datetime.strptime(name[:_TIMESTAMP_LENGTH], TIMESTAMP_FORMAT)
    except ValueError:
        return None
    rest = name[_TIMESTAMP_LENGTH:]
    if rest == "":
        return created, 1
    if rest.startswith("_") and rest[1:].isdigit():
        return created, int(rest[1:])
    return None
