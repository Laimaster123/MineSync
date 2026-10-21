"""Ресурсы приложения (шрифты, картинки, QSS, тексты) и доступ к ним.

Все пути к ресурсам нужно получать только через resource_path(),
чтобы они работали и из исходников, и из собранного PyInstaller приложения.
"""

import sys
from pathlib import Path


def resources_dir() -> Path:
    """Вернуть папку с ресурсами.

    При запуске из исходников это папка, где лежит этот файл.
    В собранном PyInstaller приложении файлы распаковываются в sys._MEIPASS,
    поэтому при сборке ресурсы нужно класть туда по пути blocksync/resources.
    """
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return Path(bundle_root) / "blocksync" / "resources"
    return Path(__file__).resolve().parent


def resource_path(relative: str) -> Path:
    """Вернуть полный путь к ресурсу, например resource_path("fonts/Silkscreen.ttf")."""
    return resources_dir() / relative
