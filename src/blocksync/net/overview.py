"""Общий список миров с обоих устройств и статус каждого.

Статус вычисляется сравнением манифестов, направление не угадывается:
программа говорит только «одинаковы» или «отличаются».
"""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from blocksync.core.compare import compare
from blocksync.core.manifest import build_manifest
from blocksync.core.world import WorldInfo, list_worlds
from blocksync.net.client import NetworkError, PeerClient
from blocksync.net.protocol import RemoteWorld

logger = logging.getLogger(__name__)


class WorldStatus(Enum):
    SAME = "same"  # файлы совпадают
    DIFFERENT = "different"  # есть различия
    ONLY_HERE = "only_here"  # мира нет на другом устройстве
    ONLY_THERE = "only_there"  # мир есть только на другом устройстве
    LOCKED = "locked"  # мир открыт в игре на одном из устройств
    UNKNOWN = "unknown"  # другое устройство не в сети, сравнить не с чем
    NOT_LINKED = "not_linked"  # сборка не связана со сборкой второго устройства


@dataclass(frozen=True)
class WorldRow:
    """Одна строка списка миров."""

    folder_name: str
    local: WorldInfo | None
    remote: RemoteWorld | None
    status: WorldStatus
    instance_id: str = ""  # сборка этого устройства, в которой лежит (или будет лежать) мир
    instance_name: str = ""

    @property
    def key(self) -> str:
        """Уникальный ключ строки: один и тот же мир может быть в разных сборках."""
        return f"{self.instance_id}/{self.folder_name}"

    @property
    def display_name(self) -> str:
        return self.local.display_name if self.local else self.remote.display_name  # type: ignore[union-attr]

    @property
    def version_name(self) -> str | None:
        return self.local.version_name if self.local else self.remote.version_name  # type: ignore[union-attr]

    @property
    def size_bytes(self) -> int:
        return self.local.size_bytes if self.local else self.remote.size_bytes  # type: ignore[union-attr]

    @property
    def info(self) -> "WorldInfo | RemoteWorld":
        """Сведения о мире: свои, а если мира здесь нет — с другого устройства."""
        return self.local if self.local is not None else self.remote  # type: ignore[return-value]

    @property
    def last_modified_ns(self) -> int | None:
        """Самое позднее изменение мира на любом из устройств."""
        times = [w.last_modified_ns for w in (self.local, self.remote) if w and w.last_modified_ns]
        return max(times) if times else None


@dataclass(frozen=True)
class Overview:
    rows: list[WorldRow]
    saves_missing: bool  # папки saves на этом устройстве нет
    peer_error: NetworkError | None  # не удалось получить миры другого устройства


def build_overview(
    saves_dir: Path, client: PeerClient | None, instance_id: str = "", instance_name: str = "",
    linked: bool = True,
) -> Overview:
    """Собрать список миров.

    client — None, если другое устройство не в сети; linked=False — сборка не связана
    со сборкой второго устройства (тогда сравнивать не с чем).
    """
    try:
        local = {w.folder_name: w for w in list_worlds(saves_dir)}
        saves_missing = False
    except FileNotFoundError:
        local, saves_missing = {}, True

    remote: dict[str, RemoteWorld] | None = None
    peer_error: NetworkError | None = None
    if client is not None:
        try:
            remote = {w.folder_name: w for w in client.list_worlds()}
        except NetworkError as error:
            peer_error = error

    rows: list[WorldRow] = []
    for name in sorted(set(local) | set(remote or {})):
        here = local.get(name)
        there = remote.get(name) if remote is not None else None
        try:
            peer_known = remote is not None and peer_error is None
            status = _status(name, here, there, peer_known, client)
            if status is WorldStatus.UNKNOWN and not linked:
                status = WorldStatus.NOT_LINKED
        except NetworkError as error:
            # Связь пропала посреди обновления: статус неизвестен, но список показываем.
            peer_error = error
            status = WorldStatus.UNKNOWN
        rows.append(WorldRow(name, here, there, status, instance_id, instance_name))
    rows.sort(key=lambda row: row.last_modified_ns or 0, reverse=True)
    return Overview(rows, saves_missing, peer_error)


def _status(
    name: str, here: WorldInfo | None, there: RemoteWorld | None, peer_known: bool, client: PeerClient | None
) -> WorldStatus:
    if (here and here.is_locked) or (there and there.is_locked):
        return WorldStatus.LOCKED
    if not peer_known:
        return WorldStatus.UNKNOWN
    if there is None:
        return WorldStatus.ONLY_HERE
    if here is None:
        return WorldStatus.ONLY_THERE
    assert client is not None
    remote_manifest = client.get_manifest(name)
    if remote_manifest.is_locked:
        return WorldStatus.LOCKED
    plan = compare(remote_manifest.manifest, build_manifest(here.path))
    return WorldStatus.DIFFERENT if plan.has_changes else WorldStatus.SAME


# icon.png, которую делает игра, весит несколько КБ; больше этого не читаем.
MAX_ICON_BYTES = 512 * 1024


def load_icons(rows: list[WorldRow], client: PeerClient | None) -> dict[str, bytes]:
    """Содержимое icon.png для миров списка: своя иконка, а если её нет — с другого устройства."""
    icons: dict[str, bytes] = {}
    for row in rows:
        try:
            if row.local is not None and row.local.icon_path is not None:
                with open(row.local.icon_path, "rb") as file:
                    icons[row.folder_name] = file.read(MAX_ICON_BYTES)
            elif row.remote is not None and row.remote.has_icon and client is not None:
                with client.open_file(row.folder_name, "icon.png", retry=False) as stream:
                    icons[row.folder_name] = stream.read(MAX_ICON_BYTES)
        except (OSError, NetworkError) as error:
            # Без иконки список всё равно можно показать.
            logger.info("Не удалось загрузить иконку мира %s: %s", row.folder_name, error)
    return icons
