"""Подпись запросов между устройствами (HMAC-SHA256).

У двух сопряжённых устройств есть общий секретный ключ. Отправитель вычисляет
по ключу и содержимому запроса «подпись» — короткую строку, которую нельзя
подделать, не зная ключа. Получатель вычисляет её заново и сравнивает.

В подпись входят метка времени и одноразовое случайное число (nonce):
так перехваченный запрос нельзя отправить повторно.
"""

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from urllib.parse import unquote

from blocksync.net.protocol import (
    HEADER_DEVICE,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
)

# Запрос старше этого считается недействительным. Часы устройств могут немного
# расходиться, поэтому окно не слишком маленькое.
MAX_REQUEST_AGE_SECONDS = 5 * 60


class AuthError(Exception):
    """Запрос не прошёл проверку подписи."""


def _message(method: str, path: str, device_id: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    """Строка, которая подписывается. Путь берём раскодированным (без %D0%9C и т.п.),
    чтобы разные способы кодирования одного адреса давали одинаковую подпись."""
    body_hash = hashlib.sha256(body).hexdigest()
    parts = [method.upper(), unquote(path), device_id, timestamp, nonce, body_hash]
    return "\n".join(parts).encode("utf-8")


def _signature(key: bytes, message: bytes) -> str:
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def sign_request(
    device_id: str, key: bytes, method: str, path: str, body: bytes = b"", now: float | None = None
) -> dict[str, str]:
    """Вернуть заголовки с подписью, которые нужно добавить к запросу."""
    timestamp = str(int(now if now is not None else time.time()))
    nonce = secrets.token_hex(16)
    message = _message(method, path, device_id, timestamp, nonce, body)
    return {
        HEADER_DEVICE: device_id,
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: _signature(key, message),
    }


class RequestVerifier:
    """Проверяет подписи входящих запросов.

    key_for_device: функция, которая по ID устройства возвращает его ключ
    или None, если устройство не сопряжено.
    """

    def __init__(
        self,
        key_for_device: Callable[[str], bytes | None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.key_for_device = key_for_device
        self.clock = clock
        self._seen_nonces: dict[str, float] = {}
        # Сервер обрабатывает запросы в нескольких потоках, поэтому
        # общий словарь одноразовых чисел защищаем блокировкой.
        self._lock = threading.Lock()

    def verify(self, method: str, path: str, headers: Mapping[str, str], body: bytes) -> str:
        """Проверить запрос. Вернуть ID устройства-отправителя или выбросить AuthError."""
        try:
            device_id = headers[HEADER_DEVICE]
            timestamp = headers[HEADER_TIMESTAMP]
            nonce = headers[HEADER_NONCE]
            signature = headers[HEADER_SIGNATURE]
        except KeyError as missing:
            raise AuthError(f"нет заголовка {missing}") from None

        key = self.key_for_device(device_id)
        if key is None:
            raise AuthError("устройство не сопряжено")

        expected = _signature(key, _message(method, path, device_id, timestamp, nonce, body))
        # compare_digest сравнивает за одинаковое время, чтобы по скорости
        # ответа нельзя было подбирать подпись.
        if not hmac.compare_digest(expected, signature):
            raise AuthError("неверная подпись")

        now = self.clock()
        try:
            age = abs(now - int(timestamp))
        except ValueError:
            raise AuthError("неверная метка времени") from None
        if age > MAX_REQUEST_AGE_SECONDS:
            raise AuthError("запрос устарел или часы на устройствах сильно расходятся")

        with self._lock:
            self._forget_old_nonces(now)
            if nonce in self._seen_nonces:
                raise AuthError("повторный запрос")
            self._seen_nonces[nonce] = now
        return device_id

    def _forget_old_nonces(self, now: float) -> None:
        """Старые одноразовые числа помнить не нужно: такие запросы и так отклонятся по времени."""
        limit = now - 2 * MAX_REQUEST_AGE_SECONDS
        for old in [n for n, seen in self._seen_nonces.items() if seen < limit]:
            del self._seen_nonces[old]
