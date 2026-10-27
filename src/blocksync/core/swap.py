"""Быстрая замена файлов мира: без копирования неизменённых файлов.

Как это работает (на одном диске перенос файла — мгновенное переименование):
1. Старые версии заменяемых и удаляемых файлов переносятся в папку бэкапа.
2. Новые версии переносятся из временной папки на их место.
3. В бэкап записывается, как откатить замену (minesync-delta.json).

Перед шагом 1 во временной папке пишется журнал. Если программу закрыли или
компьютер выключился посередине, при следующем запуске recover_interrupted()
по журналу вернёт мир в состояние до замены.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from blocksync.core.manifest import find_on_disk

logger = logging.getLogger(__name__)

JOURNAL_NAME = "journal.json"
DELTA_NAME = "minesync-delta.json"  # описание «бэкапа изменений»
BACKUP_FILES = "files"  # подпапка бэкапа со старыми версиями файлов


def swap_files(
    world_dir: Path, new_files_dir: Path, put: list[str], remove: list[str], backup_dir: Path, journal: Path
) -> None:
    """Положить в мир файлы put из new_files_dir, удалить файлы remove; старые версии — в backup_dir.

    Пути — из манифеста (через «/»). При ошибке замена откатывается и ошибка выбрасывается дальше.
    """
    # Какие из затронутых файлов сейчас есть в мире и как они называются на диске (NFC/NFD, см. find_on_disk).
    existed: dict[str, str] = {}
    for path in sorted(set(put) | set(remove)):
        real = find_on_disk(world_dir, path)
        if real.is_file():
            existed[path] = real.relative_to(world_dir).as_posix()
    state = {"world": str(world_dir), "new_files": str(new_files_dir), "backup": str(backup_dir),
             "put": put, "remove": remove, "existed": existed}
    _write_json(journal, state)
    try:
        for path, real_name in existed.items():
            _move(world_dir / real_name, backup_dir / BACKUP_FILES / path)
        for path in put:
            _move(new_files_dir / path, world_dir / path)
        _remove_empty_dirs(world_dir, remove)
        # Как откатить эту замену: вернуть старые версии и убрать файлы, которых раньше не было.
        _write_json(backup_dir / DELTA_NAME, {
            "kind": "delta",
            "restore_put": sorted(existed),
            "restore_remove": sorted(p for p in put if p not in existed),
        })
    except BaseException:
        logger.exception("Замена файлов мира %s прервалась, откатываю", world_dir)
        rollback(state)
        journal.unlink(missing_ok=True)
        raise
    journal.unlink()


def rollback(state: dict[str, Any]) -> None:
    """Вернуть мир в состояние до swap_files по сохранённому журналу (можно вызывать повторно)."""
    world, backup = Path(state["world"]), Path(state["backup"])
    existed: dict[str, str] = state["existed"]
    for path in state["put"]:
        if path not in existed:
            (world / path).unlink(missing_ok=True)  # новый файл, которого раньше не было
    for path, real in existed.items():
        old = backup / BACKUP_FILES / path
        if old.is_file():
            (world / path).unlink(missing_ok=True)  # новая версия, если уже успела встать
            (world / real).unlink(missing_ok=True)
            _move(old, world / real)
    shutil.rmtree(backup, ignore_errors=True)
    logger.info("Мир %s возвращён в состояние до синхронизации", world)


def recover_interrupted(staging_dir: Path) -> bool:
    """Если во временной папке остался журнал — откатить незавершённую замену. Вернуть True, если откатили."""
    journal = staging_dir / JOURNAL_NAME
    if not journal.is_file():
        return False
    try:
        state = json.loads(journal.read_text(encoding="utf-8"))
        rollback(state)
    except (OSError, ValueError, KeyError) as error:
        logger.error("Не удалось откатить прерванную синхронизацию по журналу %s: %s", journal, error)
        return False
    journal.unlink(missing_ok=True)
    return True


def read_delta(backup_dir: Path) -> dict[str, Any] | None:
    """Описание «бэкапа изменений» или None, если это полный бэкап мира."""
    path = backup_dir / DELTA_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # На одном диске — переименование; на разных дисках shutil.move скопирует и удалит.
    shutil.move(source, target)


def _remove_empty_dirs(world_dir: Path, removed: list[str]) -> None:
    """Удалить папки, которые опустели после удаления файлов (кроме самой папки мира)."""
    for path in removed:
        parent = (world_dir / path).parent
        while parent != world_dir and parent.is_dir():
            try:
                parent.rmdir()  # удаляется, только если пустая
            except OSError:
                break
            parent = parent.parent


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)
