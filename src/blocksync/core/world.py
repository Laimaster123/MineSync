"""Сведения о мирах в папке saves: всё, что нужно показать в списке миров."""

from dataclasses import dataclass
from pathlib import Path

from blocksync.core.level_dat import LevelDatError, LevelInfo, read_level_info
from blocksync.core.lock import is_world_locked
from blocksync.core.manifest import build_manifest, newest_mtime_ns, total_size

LEVEL_DAT_NAME = "level.dat"
ICON_NAME = "icon.png"


@dataclass(frozen=True)
class WorldInfo:
    folder_name: str  # имя папки мира; по нему миры сопоставляются между устройствами
    path: Path
    display_name: str  # название из level.dat, а если его нет — имя папки
    version_name: str | None
    data_version: int | None
    size_bytes: int
    last_modified_ns: int | None  # по самому свежему файлу внутри мира
    icon_path: Path | None
    is_locked: bool  # мир открыт в игре
    game_type: int | None = None  # режим игры (см. level_dat.LevelInfo)
    hardcore: bool = False
    play_ticks: int | None = None  # время в мире в игровых тиках


def is_safe_world_name(name: str) -> bool:
    """Имя папки мира, пришедшее по сети, должно указывать прямо внутрь saves."""
    return name not in ("", ".", "..") and "/" not in name and "\\" not in name


def is_world_dir(path: Path) -> bool:
    """Папка считается миром, если в ней есть level.dat."""
    return path.is_dir() and (path / LEVEL_DAT_NAME).is_file()


def read_world_info(world_dir: Path) -> WorldInfo:
    """Собрать сведения об одном мире."""
    try:
        level = read_level_info(world_dir / LEVEL_DAT_NAME)
    except LevelDatError:
        # Повреждённый level.dat не должен ломать весь список миров.
        level = LevelInfo(None, None, None)

    manifest = build_manifest(world_dir)
    icon = world_dir / ICON_NAME
    return WorldInfo(
        folder_name=world_dir.name,
        path=world_dir,
        display_name=level.level_name or world_dir.name,
        version_name=level.version_name,
        data_version=level.data_version,
        size_bytes=total_size(manifest),
        last_modified_ns=newest_mtime_ns(manifest),
        icon_path=icon if icon.is_file() else None,
        is_locked=is_world_locked(world_dir),
        game_type=level.game_type,
        hardcore=level.hardcore,
        play_ticks=level.play_ticks,
    )


def read_data_version(world_dir: Path) -> int | None:
    """Версия формата мира (DataVersion) или None, если мира нет или level.dat не читается."""
    try:
        return read_level_info(world_dir / LEVEL_DAT_NAME).data_version
    except (LevelDatError, OSError):
        return None


def list_worlds(saves_dir: Path) -> list[WorldInfo]:
    """Все миры в папке saves, сначала недавно изменённые.

    Если папки saves нет, выбрасывает FileNotFoundError.
    """
    if not saves_dir.is_dir():
        raise FileNotFoundError(f"папка с мирами не найдена: {saves_dir}")
    worlds = [read_world_info(child) for child in saves_dir.iterdir() if is_world_dir(child)]
    worlds.sort(key=lambda world: world.last_modified_ns or 0, reverse=True)
    return worlds
