"""Понятные тексты ошибок для пользователя вместо трейсбеков."""

import logging

from blocksync.core.apply import SyncCancelled, SyncError
from blocksync.core.checks import NotEnoughSpaceError
from blocksync.core.lock import WorldLockedError
from blocksync.net.client import NetworkError
from blocksync.net.pairing import PairingError
from blocksync.ui.format import format_size
from blocksync.ui.strings import tr

logger = logging.getLogger(__name__)


def describe_error(error: BaseException) -> str:
    if isinstance(error, SyncCancelled):
        return tr("error.cancelled")
    if isinstance(error, WorldLockedError):
        return tr("error.world_locked")
    if isinstance(error, NotEnoughSpaceError):
        return tr(
            "error.no_space", required=format_size(error.required_bytes), free=format_size(error.free_bytes)
        )
    if isinstance(error, (NetworkError, PairingError, SyncError)):
        logger.warning("Ошибка: %s", error)
        return _sentence(str(error))
    if isinstance(error, OSError):
        logger.warning("Ошибка файла", exc_info=error)
        return tr("error.file", details=error.strerror or str(error), path=error.filename or "")
    logger.error("Непредвиденная ошибка", exc_info=error)
    return tr("error.unexpected")


def _sentence(text: str) -> str:
    """«не удалось подключиться» -> «Не удалось подключиться.»"""
    text = text.strip()
    if not text:
        return tr("error.unexpected")
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."
