"""Проверка, открыт ли мир в игре.

Пока мир открыт, Minecraft держит «блокировку» на файле session.lock внутри
папки мира. Блокировка — это пометка в операционной системе «этот файл занят
процессом X». Мы пытаемся поставить свою блокировку на короткое время:
если не получилось — файл занят игрой. Содержимое файла не меняется.

Способ на macOS и Windows разный, потому что разные системы блокировок.
"""

import sys
from pathlib import Path

LOCK_FILE_NAME = "session.lock"


class WorldLockedError(Exception):
    """Мир открыт в игре, менять его нельзя."""

    def __init__(self, world_dir: Path) -> None:
        super().__init__(f"мир открыт в игре: {world_dir}")
        self.world_dir = world_dir


def is_world_locked(world_dir: Path) -> bool:
    """True, если мир сейчас открыт в игре."""
    lock_file = world_dir / LOCK_FILE_NAME
    if not lock_file.is_file():
        return False
    if sys.platform == "win32":
        return _is_locked_windows(lock_file)
    return _is_locked_posix(lock_file)


# fcntl нет на Windows, msvcrt есть только на Windows: каждая функция определена только там,
# где работает (так и проверка типов на любой системе пропускает чужую функцию).
if sys.platform != "win32":

    def _is_locked_posix(lock_file: Path) -> bool:
        """macOS и Linux: блокировка через fcntl (её же использует Java, на которой написан Minecraft)."""
        import fcntl

        # Открываем только для чтения: так файл точно не изменится.
        # Разделяемая блокировка (LOCK_SH) конфликтует с исключительной блокировкой игры.
        with open(lock_file, "rb") as file:
            try:
                fcntl.lockf(file, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.lockf(file, fcntl.LOCK_UN)
            return False


else:

    def _is_locked_windows(lock_file: Path) -> bool:
        """Windows: пробуем заблокировать первый байт файла через msvcrt."""
        import msvcrt

        try:
            file = open(lock_file, "rb")
        except PermissionError:
            # Файл нельзя даже открыть: его держит игра.
            return True
        with file:
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBRLCK, 1)
            except OSError:
                return True
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            return False
