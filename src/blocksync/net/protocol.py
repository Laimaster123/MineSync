"""Протокол обмена между устройствами: адреса запросов и формат данных.

Устройства общаются по HTTP. Ответы (кроме файлов) — JSON, текст вида
{"name": "Мир", "size": 123}, который легко превратить в словарь Python.

Запросы:
  GET  /api/v1/worlds                         список миров
  GET  /api/v1/worlds/<мир>/manifest          манифест мира, версия, занят ли он
  GET  /api/v1/worlds/<мир>/files/<путь>      содержимое файла (передаётся кусками)
  POST /api/v1/sync-requests  {"world": ...}  просьба «забери мир у меня»
  POST /api/v1/unpair                         «забудь меня»: второе устройство удалило пару
  GET  /api/v1/instances                      сборки (Prism и др.): [{"id", "name"}]
  POST /api/v1/instance-links                 «твоя сборка X связана с моей Y» (или связь удалена)
  POST /api/v1/changed                        «у меня изменились миры» — обнови список

Запросы к мирам принимают параметр ?instance=<ID сборки>; без него — папка saves по умолчанию.

Передача файла (с версии 1.1). Клиент может прислать заголовки:
  X-MineSync-Transfer: frames1   — передавать «кадрами» (сжатие и контрольная сумма, см. ниже);
  Range: bytes=<N>-              — продолжить с байта N (докачка после обрыва связи);
  X-MineSync-Expect: <размер> <время изменения> — каким клиент ждёт файл (по манифесту);
                                   если файл с тех пор изменился, сервер ответит 409.
Кадр: 1 байт вида + 4 байта длины + содержимое. Виды: 0 — кусок файла как есть,
1 — кусок, сжатый zlib, 2 — конец: SHA-256 переданных байт (32 байта) и 1 байт флагов
(флаг 1 — файл изменился, пока передавался). Старая версия программы этих заголовков
не знает и просто отдаёт файл целиком — клиент умеет работать и так.
Просьба забрать мир: {"world", "instance": сборка отправителя, "target_instance": сборка получателя}.

Сопряжение (без подписи — общего ключа ещё нет, см. pairing.py):
  POST /api/v1/pair/start                     обмен сообщениями SPAKE2
  POST /api/v1/pair/confirm                   подтверждение кода и обмен сертификатами
"""

import struct
from dataclasses import dataclass, fields
from typing import Any
from urllib.parse import quote

from blocksync.core.manifest import FileEntry, Manifest
from blocksync.core.world import WorldInfo

API_PREFIX = "/api/v1"
WORLDS_PATH = f"{API_PREFIX}/worlds"
SYNC_REQUESTS_PATH = f"{API_PREFIX}/sync-requests"
UNPAIR_PATH = f"{API_PREFIX}/unpair"
INSTANCES_PATH = f"{API_PREFIX}/instances"
INSTANCE_LINKS_PATH = f"{API_PREFIX}/instance-links"
CHANGED_PATH = f"{API_PREFIX}/changed"
PAIR_START_PATH = f"{API_PREFIX}/pair/start"
PAIR_CONFIRM_PATH = f"{API_PREFIX}/pair/confirm"

# Заголовки подписи запроса (см. auth.py).
HEADER_DEVICE = "X-BlockSync-Device"
HEADER_TIMESTAMP = "X-BlockSync-Timestamp"
HEADER_NONCE = "X-BlockSync-Nonce"
HEADER_SIGNATURE = "X-BlockSync-Signature"

# Передача файлов кадрами (см. описание в начале модуля).
HEADER_TRANSFER = "X-MineSync-Transfer"
HEADER_EXPECT = "X-MineSync-Expect"
TRANSFER_FRAMES = "frames1"
FRAME_RAW = 0
FRAME_ZLIB = 1
FRAME_END = 2
FRAME_FLAG_CHANGED = 1
FRAME_HEADER = struct.Struct(">BI")  # вид (1 байт) и длина содержимого (4 байта)
# Больше этого содержимое кадра быть не может (кусок файла — не больше 1 МБ).
MAX_FRAME_BYTES = 1024 * 1024 + 4096


def expect_header(size: int, mtime_ns: int) -> str:
    """Значение заголовка X-MineSync-Expect: каким должен быть файл."""
    return f"{size} {mtime_ns}"


def with_instance(path: str, instance: str | None) -> str:
    """Добавить к адресу выбранную сборку: /api/v1/worlds?instance=prism%3A26.3"""
    return path if instance is None else f"{path}?instance={quote(instance, safe='')}"


def manifest_path(world: str) -> str:
    # quote заменяет пробелы, кириллицу и "/" на коды вида %20, чтобы адрес был корректным.
    return f"{WORLDS_PATH}/{quote(world, safe='')}/manifest"


def file_path(world: str, relative_path: str) -> str:
    # В пути файла "/" оставляем как есть: это разделитель папок.
    return f"{WORLDS_PATH}/{quote(world, safe='')}/files/{quote(relative_path, safe='/')}"


@dataclass(frozen=True)
class RemoteWorld:
    """Мир на другом устройстве (то, что приходит в списке миров)."""

    folder_name: str
    display_name: str
    version_name: str | None
    data_version: int | None
    size_bytes: int
    last_modified_ns: int | None
    has_icon: bool
    is_locked: bool
    note: str | None = None  # описание мира, которое пользователь дал на том устройстве
    game_type: int | None = None
    hardcore: bool = False
    play_ticks: int | None = None


@dataclass(frozen=True)
class RemoteInstance:
    """Сборка на другом устройстве."""

    id: str
    name: str


@dataclass(frozen=True)
class SyncRequest:
    """Просьба второго устройства «забери мир у меня»."""

    world: str
    instance: str | None = None  # сборка на отправителе, откуда брать мир
    target_instance: str | None = None  # сборка на получателе, куда положить мир


@dataclass(frozen=True)
class RemoteManifest:
    """Манифест мира на другом устройстве вместе с версией и статусом блокировки."""

    manifest: Manifest
    data_version: int | None
    is_locked: bool


def world_to_json(world: WorldInfo, note: str | None = None) -> dict[str, Any]:
    return {
        "folder_name": world.folder_name,
        "display_name": world.display_name,
        "version_name": world.version_name,
        "data_version": world.data_version,
        "size_bytes": world.size_bytes,
        "last_modified_ns": world.last_modified_ns,
        "has_icon": world.icon_path is not None,
        "is_locked": world.is_locked,
        "note": note,
        "game_type": world.game_type,
        "hardcore": world.hardcore,
        "play_ticks": world.play_ticks,
    }


def world_from_json(data: dict[str, Any]) -> RemoteWorld:
    # Неизвестные поля пропускаем: их может прислать более новая версия программы.
    known = {f.name for f in fields(RemoteWorld)}
    return RemoteWorld(**{key: value for key, value in data.items() if key in known})


def manifest_to_json(manifest: Manifest, data_version: int | None, is_locked: bool) -> dict[str, Any]:
    return {
        "files": [[e.path, e.size, e.mtime_ns] for e in manifest.values()],
        "data_version": data_version,
        "is_locked": is_locked,
    }


def manifest_from_json(data: dict[str, Any]) -> RemoteManifest:
    manifest = {path: FileEntry(path, size, mtime_ns) for path, size, mtime_ns in data["files"]}
    return RemoteManifest(manifest, data["data_version"], data["is_locked"])
