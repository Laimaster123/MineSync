"""Тексты интерфейса.

Все надписи лежат в resources/i18n/ru.json, а в коде используются по ключу:
    tr("main.sync_row")                -> "Синхронизировать"
    tr("sync.where_played", world="X") -> "Где ты играл последним в «X»?"
Английские тексты — в en.json с теми же ключами. Язык меняется set_language().
"""

import json
import logging
from functools import cache
from typing import Any

from blocksync.resources import resource_path

logger = logging.getLogger(__name__)

LANGUAGES = ("ru", "en")
_language = "ru"


@cache
def _strings(language: str) -> dict[str, str]:
    return json.loads(resource_path(f"i18n/{language}.json").read_text(encoding="utf-8"))


def set_language(language: str) -> None:
    global _language
    _language = language if language in LANGUAGES else "ru"


def current_language() -> str:
    return _language


def has_text(key: str) -> bool:
    """Есть ли текст с таким ключом (хотя бы на русском)."""
    return key in _strings(_language) or key in _strings("ru")


def tr(key: str, **values: Any) -> str:
    """Текст по ключу; {name} в тексте заменяются значениями из values."""
    text = _strings(_language).get(key) or _strings("ru").get(key)
    if text is None:
        logger.error("Нет текста для ключа %s", key)
        return key
    return text.format(**values) if values else text
