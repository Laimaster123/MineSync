"""Защита от второй копии программы: файл-«замок» в папке данных.

Первая копия открывает файл minesync.lock и ставит на него блокировку системы.
Вторая копия пытается сделать то же, получает отказ и сообщает «уже запущен».
Если программа упадёт, система снимет блокировку сама, поэтому «вечного замка» не будет.
"""

import os
import sys
from pathlib import Path
from typing import BinaryIO

LOCK_FILE_NAME = "minesync.lock"


class AlreadyRunningError(Exception):
    """MineSync с этой папкой данных уже запущен."""


class AppLock:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / LOCK_FILE_NAME
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        """Занять замок или выбросить AlreadyRunningError."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                # flock, а не lockf: она действует и между двумя копиями в одном процессе (так в тестах).
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            raise AlreadyRunningError("MineSync уже запущен на этом компьютере") from None
        file.seek(0)
        file.truncate()
        file.write(str(os.getpid()).encode())  # номер процесса — для удобства, при поиске проблем
        file.flush()
        self._file = file

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # замок всё равно снимется при закрытии файла
        self._file.close()
        self._file = None
