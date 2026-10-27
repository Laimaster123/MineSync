"""Общие помощники для тестов: создание тестовых миров во временных папках."""

import gzip
import os
import struct
import subprocess
import sys
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Время, которое ставим файлам в тестах (секунды с 1970 года), чтобы
# результат не зависел от того, когда запущены тесты.
BASE_TIME = 1_700_000_000


def write_file(path: Path, content: bytes = b"data", mtime: float = BASE_TIME) -> None:
    """Создать файл (вместе с папками) и выставить ему время изменения."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))


def make_world(world_dir: Path, files: dict[str, bytes], mtime: float = BASE_TIME) -> Path:
    """Создать тестовый мир: словарь «относительный путь -> содержимое»."""
    world_dir.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        write_file(world_dir / relative, content, mtime)
    return world_dir


def read_world(world_dir: Path) -> dict[str, bytes]:
    """Прочитать все файлы мира в словарь, чтобы сравнивать миры в тестах.

    Имена приводятся к NFC: одинаковые имена в NFC и NFD считаются одним файлом.
    """
    return {
        unicodedata.normalize("NFC", path.relative_to(world_dir).as_posix()): path.read_bytes()
        for path in sorted(world_dir.rglob("*"))
        if path.is_file()
    }


def nbt_string(text: str) -> bytes:
    """Строка NBT: длина (2 байта) и текст в UTF-8."""
    encoded = text.encode("utf-8")
    return struct.pack(">H", len(encoded)) + encoded


def nbt_compound(values: dict[str, Any]) -> bytes:
    """Содержимое словаря NBT. Поддерживает str, int (как TAG_INT) и вложенные dict."""
    result = b""
    for name, value in values.items():
        if isinstance(value, dict):
            result += b"\x0a" + nbt_string(name) + nbt_compound(value)
        elif isinstance(value, str):
            result += b"\x08" + nbt_string(name) + nbt_string(value)
        elif isinstance(value, int):
            result += b"\x03" + nbt_string(name) + struct.pack(">i", value)
        else:
            raise TypeError(f"тип не поддерживается: {type(value)}")
    return result + b"\x00"


def make_level_dat(
    world_dir: Path,
    level_name: str = "Тестовый мир",
    version_name: str = "1.21.4",
    data_version: int = 4189,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Создать в папке мира упрощённый level.dat (gzip + NBT). extra — дополнительные поля Data."""
    root = {
        "Data": {
            "LevelName": level_name,
            "DataVersion": data_version,
            "Version": {"Name": version_name, "Id": data_version},
            **(extra or {}),
        }
    }
    raw = b"\x0a" + nbt_string("") + nbt_compound(root)
    path = world_dir / "level.dat"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(raw))
    os.utime(path, (BASE_TIME, BASE_TIME))
    return path


# Программа, которая играет роль Minecraft: держит блокировку session.lock,
# пока тест не закроет её стандартный ввод. Запускается отдельным процессом,
# потому что блокировку своего же процесса система «не видит».
_HOLD_LOCK_SCRIPT = """
import sys
file = open(sys.argv[1], "r+b")
if sys.platform == "win32":
    import msvcrt
    msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
else:
    import fcntl
    fcntl.lockf(file, fcntl.LOCK_EX)
print("locked", flush=True)
sys.stdin.read()
"""


@contextmanager
def hold_lock(lock_file: Path) -> Iterator[None]:
    """Пока выполняется блок with, файл заблокирован другим процессом."""
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK_SCRIPT, str(lock_file)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        yield
    finally:
        holder.communicate(timeout=10)  # закрывает ввод, процесс завершается


def leftovers(folder: Path) -> list[Path]:
    """Что осталось в папке для временных папок (опустевшую папку программа удаляет сама)."""
    return sorted(folder.iterdir()) if folder.exists() else []
