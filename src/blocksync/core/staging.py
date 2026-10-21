"""Временные папки, в которых готовится новая версия мира (или новые версии файлов).

Если что-то пошло не так, временная папка удаляется, а мир остаётся нетронутым.
Если замена файлов прервалась на середине, в папке остаётся журнал, и при
следующем запуске мир откатывается по нему (см. core/swap.py).
"""

import logging
import shutil
import tempfile
from pathlib import Path

from blocksync.core.swap import recover_interrupted

logger = logging.getLogger(__name__)

# По этому началу имени узнаём свои временные папки при уборке.
STAGING_PREFIX = "staging-"


def create_staging_dir(tmp_root: Path) -> Path:
    """Создать новую пустую временную папку внутри tmp_root."""
    tmp_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=tmp_root))


def cleanup_leftovers(tmp_root: Path) -> int:
    """Удалить временные папки, оставшиеся от прерванных синхронизаций.

    Вызывается при запуске приложения. Возвращает число удалённых папок.
    """
    if not tmp_root.is_dir():
        return 0
    removed = 0
    for child in tmp_root.iterdir():
        if child.is_dir() and child.name.startswith(STAGING_PREFIX):
            # Если замена файлов прервалась на середине — сначала вернуть мир как было.
            if recover_interrupted(child):
                logger.warning("Прерванная синхронизация откачена по журналу: %s", child)
            shutil.rmtree(child, ignore_errors=True)
            logger.info("Удалены остатки незавершённой синхронизации: %s", child)
            removed += 1
    return removed
