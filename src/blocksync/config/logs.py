"""Журнал работы (лог) в файл в папке данных приложения.

В лог не пишутся секретные ключи и коды сопряжения.
Файл ограничен по размеру: когда он вырастает до 1 МБ, начинается новый,
а храним только несколько последних.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

LOG_FILE_NAME = "minesync.log"
MAX_LOG_BYTES = 1024 * 1024
LOG_FILES_KEPT = 3


def setup_logging(logs_dir: Path) -> Path:
    """Направить лог в файл и записывать туда непредвиденные ошибки. Вернуть путь к файлу."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / LOG_FILE_NAME
    handler = RotatingFileHandler(log_file, maxBytes=MAX_LOG_BYTES, backupCount=LOG_FILES_KEPT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # httpx пишет строку на каждый запрос, то есть на каждый файл мира, — это лишнее.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.excepthook = _log_uncaught
    return log_file


def _log_uncaught(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> None:
    logging.getLogger("blocksync").critical("Непредвиденная ошибка", exc_info=(exc_type, exc, tb))
    sys.__excepthook__(exc_type, exc, tb)
