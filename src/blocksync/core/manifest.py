"""Манифест мира: список всех его файлов с размером и временем изменения.

Манифест позволяет сравнить две копии мира, не читая содержимое файлов:
если у файла совпадают размер и время изменения, считаем его одинаковым.
"""

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Эти файлы никогда не синхронизируются.
# session.lock — служебный файл игры, остальные создают macOS и Windows.
IGNORED_NAMES: frozenset[str] = frozenset({"session.lock", ".DS_Store", "Thumbs.db"})


@dataclass(frozen=True)
class FileEntry:
    """Один файл мира."""

    path: str  # путь внутри папки мира, через "/", в форме Unicode NFC
    size: int  # размер в байтах
    mtime_ns: int  # время изменения в наносекундах


# Манифест: путь файла -> запись о нём.
Manifest = dict[str, FileEntry]


def normalize_path(relative: str) -> str:
    """Привести относительный путь к единому виду.

    Разделитель всегда "/" (Windows использует "\\").
    Буквы вроде «й» и «ё» можно записать в Unicode двумя способами:
    одним символом (NFC) или буквой плюс значком (NFD). macOS иногда
    использует NFD, Windows обычно NFC. Чтобы одно и то же имя не считалось
    двумя разными файлами, всегда приводим его к NFC.
    """
    return unicodedata.normalize("NFC", relative.replace("\\", "/"))


def find_on_disk(world_dir: Path, relative: str) -> Path:
    """Найти настоящий путь файла по пути из манифеста (он в форме NFC).

    macOS находит файл по любой форме имени, а Windows — только по той,
    с которой файл записан. Поэтому, если прямой путь не существует,
    ищем в каждой папке элемент, имя которого совпадает после приведения к NFC.
    Если ничего не нашлось, возвращаем прямой путь (дальше будет обычная ошибка).
    """
    direct = world_dir / relative
    if direct.exists():
        return direct
    current = world_dir
    for part in relative.split("/"):
        candidate = current / part
        if not candidate.exists():
            try:
                names = os.listdir(current)
            except OSError:
                return direct
            match = next((name for name in names if unicodedata.normalize("NFC", name) == part), None)
            if match is None:
                return direct
            candidate = current / match
        current = candidate
    return current


def build_manifest(world_dir: Path) -> Manifest:
    """Обойти папку мира и собрать манифест всех файлов в ней."""
    manifest: Manifest = {}
    # os.walk обходит все вложенные папки. По символическим ссылкам на папки
    # он не заходит, а ссылки на файлы мы пропускаем ниже сами.
    for dir_path, _dir_names, file_names in os.walk(world_dir):
        for name in file_names:
            full_path = Path(dir_path) / name
            if name in IGNORED_NAMES or full_path.is_symlink():
                continue
            stat = full_path.stat()
            relative = normalize_path(full_path.relative_to(world_dir).as_posix())
            manifest[relative] = FileEntry(relative, stat.st_size, stat.st_mtime_ns)
    return manifest


def total_size(manifest: Manifest) -> int:
    """Общий размер всех файлов манифеста в байтах."""
    return sum(entry.size for entry in manifest.values())


def newest_mtime_ns(manifest: Manifest) -> int | None:
    """Время изменения самого свежего файла или None, если файлов нет."""
    if not manifest:
        return None
    return max(entry.mtime_ns for entry in manifest.values())
