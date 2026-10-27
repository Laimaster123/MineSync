"""Каким был мир сразу после последней синхронизации.

После синхронизации мир на обоих компьютерах одинаковый. Его манифест (список файлов
с размером и временем изменения) сохраняется. В следующий раз по нему видно, где мир
изменился с тех пор: только здесь — значит, играли здесь; только там — играли там;
и там, и там — конфликт, о котором нужно предупредить.

Манифест хранит компьютер, который получал мир (он видит итог синхронизации).
Второй компьютер спрашивает его по сети (GET .../sync-state), поэтому подсказка
работает на обоих.

Файлы лежат в папке sync_state внутри папки данных: по одному сжатому JSON на мир.
"""

import gzip
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from blocksync.core.manifest import FileEntry, Manifest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncState:
    time: datetime  # когда закончилась синхронизация
    manifest: Manifest  # каким стал мир


def state_to_json(state: SyncState) -> dict[str, Any]:
    return {
        "time": state.time.isoformat(timespec="seconds"),
        "files": [[e.path, e.size, e.mtime_ns] for e in state.manifest.values()],
    }


def state_from_json(data: dict[str, Any]) -> SyncState:
    manifest = {path: FileEntry(path, size, mtime_ns) for path, size, mtime_ns in data["files"]}
    return SyncState(datetime.fromisoformat(data["time"]), manifest)


class SyncStateStore:
    """Состояния миров после синхронизации: по сборке этого компьютера и папке мира."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def save(self, instance: str | None, world: str, manifest: Manifest, time: datetime | None = None) -> None:
        state = SyncState((time or datetime.now()).replace(microsecond=0), manifest)
        data = {"instance": instance or "", "world": world, **state_to_json(state)}
        path = self._path(instance, world)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode("utf-8")))
        os.replace(temp, path)

    def load(self, instance: str | None, world: str) -> SyncState | None:
        """Состояние мира после последней синхронизации или None (не синхронизировался или файл испорчен)."""
        path = self._path(instance, world)
        if not path.is_file():
            return None
        try:
            return state_from_json(json.loads(gzip.decompress(path.read_bytes())))
        except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
            logger.warning("Не удалось прочитать состояние мира %s: %s", path, error)
            return None

    def _path(self, instance: str | None, world: str) -> Path:
        # Имя мира может содержать что угодно, поэтому имя файла — хэш (сборка + папка мира).
        key = hashlib.sha256(f"{instance or ''}\0{world}".encode()).hexdigest()[:32]
        return self.folder / f"{key}.json.gz"


def newest(*states: SyncState | None) -> SyncState | None:
    """Самое свежее из состояний (те, что None, пропускаются)."""
    known = [state for state in states if state is not None]
    return max(known, key=lambda state: state.time) if known else None
