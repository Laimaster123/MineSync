"""Пути к папкам приложения.

Все данные приложения (настройки, ключи, бэкапы, временные файлы, лог) лежат
в одной «папке данных». Её стандартное место зависит от системы:
  macOS:   ~/Library/Application Support/MineSync
  Windows: C:\\Users\\<имя>\\AppData\\Local\\MineSync
"""

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "MineSync"
# Прежнее название программы: папку данных с этим именем переносим под новое.
OLD_APP_NAME = "BlockSync"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppPaths:
    """Папки приложения внутри папки данных root."""

    root: Path

    @classmethod
    def default(cls) -> "AppPaths":
        root = platformdirs.user_data_path(APP_NAME, appauthor=False)
        migrate_data_dir(platformdirs.user_data_path(OLD_APP_NAME, appauthor=False), root)
        return cls(root)

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def history_file(self) -> Path:
        return self.root / "history.json"

    @property
    def sync_state_dir(self) -> Path:
        return self.root / "sync_state"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    @property
    def default_backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def tls_dir(self) -> Path:
        return self.root / "tls"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"


def migrate_data_dir(old_root: Path, new_root: Path) -> None:
    """Переименовать папку данных со старым названием программы, чтобы не потерять пару и бэкапы."""
    if new_root.exists() or not old_root.is_dir():
        return
    try:
        old_root.rename(new_root)
        logger.info("Папка данных перенесена: %s -> %s", old_root, new_root)
    except OSError as error:
        # Например, старая версия программы ещё запущена. Начнём с новой папки, старая останется.
        logger.warning("Не удалось перенести папку данных %s: %s", old_root, error)


def default_saves_dir() -> Path:
    """Папка saves стандартного лаунчера Minecraft."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / ".minecraft" / "saves"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "minecraft" / "saves"
    return Path.home() / ".minecraft" / "saves"
