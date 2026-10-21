"""История синхронизаций: что, когда и откуда переносилось.

Хранится в history.json в папке данных приложения. Каждое устройство пишет
свою историю: полученные миры (с числом файлов и объёмом) и отправленные
просьбы «забери мир у меня» (подробности о них записывает получатель).
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# Храним последние записи, чтобы файл не рос бесконечно.
MAX_RECORDS = 500


class Direction(Enum):
    RECEIVED = "received"  # мир перенесён на это устройство
    SENT = "sent"  # это устройство попросило второе забрать мир


@dataclass(frozen=True)
class SyncRecord:
    time: str  # дата и время в формате ISO, например "2026-10-21T20:15:03"
    world: str  # название мира
    direction: Direction
    peer: str  # имя второго устройства
    copied_files: int = 0
    deleted_files: int = 0
    bytes_copied: int = 0
    duration_seconds: float = 0.0
    folder: str = ""  # папка мира (в старых записях её нет — тогда сопоставляем по названию)
    instance: str = ""  # ID сборки этого устройства

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.time)


@dataclass(frozen=True)
class HistoryStats:
    received: int  # сколько раз миры приходили на это устройство
    sent: int  # сколько раз это устройство отправляло миры
    bytes_received: int
    last: SyncRecord | None


def new_record(world: str, direction: Direction, peer: str, **details: float | str) -> SyncRecord:
    now = datetime.now().replace(microsecond=0).isoformat()
    return SyncRecord(now, world, direction, peer, **details)  # type: ignore[arg-type]


def last_synced(records: list[SyncRecord], instance: str, folder: str, world: str) -> datetime | None:
    """Когда мир последний раз синхронизировался (в любую сторону); None — ни разу."""
    for record in records:  # записи идут от новых к старым
        if record.folder:
            if record.folder == folder and record.instance in ("", instance):
                return record.when
        elif record.world == world:
            return record.when
    return None


def load_history(path: Path) -> list[SyncRecord]:
    """Записи истории, сначала новые. Повреждённый файл не мешает работе программы."""
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        records = [SyncRecord(**{**item, "direction": Direction(item["direction"])}) for item in items]
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.warning("Не удалось прочитать историю %s: %s", path, error)
        return []
    return sorted(records, key=lambda r: r.time, reverse=True)


def add_record(path: Path, record: SyncRecord) -> None:
    """Добавить запись. Запись — через временный файл, чтобы сбой не испортил историю."""
    records = [record, *load_history(path)][:MAX_RECORDS]
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    data = [{**asdict(r), "direction": r.direction.value} for r in records]
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def stats(records: list[SyncRecord]) -> HistoryStats:
    received = [r for r in records if r.direction is Direction.RECEIVED]
    return HistoryStats(
        received=len(received),
        sent=len(records) - len(received),
        bytes_received=sum(r.bytes_copied for r in received),
        last=records[0] if records else None,
    )
