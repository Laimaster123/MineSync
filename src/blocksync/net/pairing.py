"""Сопряжение двух устройств по 6-значному коду.

Устройство-«хозяин» показывает код, на втором устройстве («гость») его вводят.
Сам код по сети НЕ передаётся. Вместо этого используется SPAKE2 — алгоритм,
который позволяет двум сторонам, знающим один и тот же короткий код,
договориться об общем длинном секретном ключе:
  * подслушивающий в сети ничего не узнаёт ни о коде, ни о ключе;
  * подставное устройство может проверить только одну догадку о коде за попытку,
    а попыток всего 5 — угадать код из миллиона вариантов не выйдет.

Шаги:
  1. start:   гость и хозяин обмениваются сообщениями SPAKE2 и получают ключ.
              Если коды были разные, ключи тоже получатся разные.
  2. confirm: гость доказывает, что у него тот же ключ (присылает HMAC),
              и передаёт свой сертификат. Хозяин проверяет, отвечает своим
              HMAC и сертификатом. Гость проверяет ответ.
После этого у обоих есть общий ключ для подписи запросов и сертификат собеседника.
"""

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_A, SPAKE2_B

from blocksync.config.settings import PairedDevice
from blocksync.net.client import CONNECT_TIMEOUT, READ_TIMEOUT, NetworkError
from blocksync.net.protocol import PAIR_CONFIRM_PATH, PAIR_START_PATH
from blocksync.net.tls import insecure_client_context

CODE_LIFETIME_SECONDS = 120
MAX_ATTEMPTS = 5
CODE_DIGITS = 6
# Сколько незавершённых попыток сопряжения помнит хозяин одновременно.
MAX_PENDING = 5

_ID_GUEST = b"blocksync-guest"
_ID_HOST = b"blocksync-host"


class PairingError(Exception):
    """Сопряжение не удалось. status — HTTP-код для ответа гостю."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class _Keys:
    shared: bytes  # общий ключ пары: им потом подписываются все запросы
    confirm: bytes  # ключ только для подтверждения при сопряжении


def _derive_keys(spake_key: bytes, transcript: bytes) -> _Keys:
    """Получить из результата SPAKE2 два независимых ключа (HKDF — стандартный способ)."""

    def derive(purpose: bytes) -> bytes:
        return HKDF(hashes.SHA256(), length=32, salt=None, info=purpose + transcript).derive(spake_key)

    return _Keys(shared=derive(b"blocksync shared key"), confirm=derive(b"blocksync confirm"))


def _transcript(guest_id: str, host_id: str, guest_msg: bytes, host_msg: bytes) -> bytes:
    """Всё, о чём договорились стороны: подмена любой части сломает подтверждение."""
    return b"|".join([guest_id.encode(), host_id.encode(), guest_msg, host_msg])


def _mac(key: bytes, role: bytes, transcript: bytes, cert_pem: str) -> str:
    cert_hash = hashlib.sha256(cert_pem.encode()).digest()
    return hmac.new(key, role + transcript + cert_hash, hashlib.sha256).hexdigest()


def generate_code() -> str:
    """Случайный код из 6 цифр, например "042917"."""
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


@dataclass
class _Pending:
    guest_name: str
    transcript: bytes
    keys: _Keys


class PairingHost:
    """Сторона, которая показывает код и ждёт, пока его введут на другом устройстве."""

    def __init__(
        self,
        device_id: str,
        device_name: str,
        cert_pem: str,
        on_paired: Callable[[PairedDevice], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.device_id = device_id
        self.device_name = device_name
        self.cert_pem = cert_pem
        self.on_paired = on_paired
        self.clock = clock
        self._code: str | None = None
        self._expires_at = 0.0
        self._attempts_left = 0
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def start_code(self) -> str:
        """Создать новый код (старый перестаёт действовать)."""
        with self._lock:
            self._code = generate_code()
            self._expires_at = self.clock() + CODE_LIFETIME_SECONDS
            self._attempts_left = MAX_ATTEMPTS
            self._pending.clear()
            return self._code

    def cancel(self) -> None:
        with self._lock:
            self._invalidate()

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active_code() is not None

    def handle_start(self, data: dict[str, Any]) -> dict[str, Any]:
        """Шаг 1: получить сообщение SPAKE2 гостя, ответить своим."""
        guest_id, guest_name, guest_msg = _require(data, "device_id", "name", "msg")
        with self._lock:
            code = self._active_code()
            if code is None:
                raise PairingError("на устройстве нет активного кода сопряжения", HTTPStatus.CONFLICT)
            spake = SPAKE2_B(code.encode(), idA=_ID_GUEST, idB=_ID_HOST)
            host_msg = spake.start()
            try:
                spake_key = spake.finish(bytes.fromhex(guest_msg))
            except Exception:
                raise PairingError("повреждённое сообщение сопряжения") from None
            transcript = _transcript(guest_id, self.device_id, bytes.fromhex(guest_msg), host_msg)
            if guest_id not in self._pending and len(self._pending) >= MAX_PENDING:
                raise PairingError("слишком много попыток сопряжения", HTTPStatus.TOO_MANY_REQUESTS)
            self._pending[guest_id] = _Pending(guest_name, transcript, _derive_keys(spake_key, transcript))
        return {"device_id": self.device_id, "name": self.device_name, "msg": host_msg.hex()}

    def handle_confirm(self, data: dict[str, Any]) -> dict[str, Any]:
        """Шаг 2: проверить подтверждение гостя; при успехе запомнить пару."""
        guest_id, guest_cert, guest_mac = _require(data, "device_id", "cert", "mac")
        with self._lock:
            if self._active_code() is None:
                raise PairingError("код сопряжения устарел", HTTPStatus.GONE)
            pending = self._pending.pop(guest_id, None)
            if pending is None:
                raise PairingError("сопряжение не начато", HTTPStatus.CONFLICT)
            expected = _mac(pending.keys.confirm, b"guest", pending.transcript, guest_cert)
            if not hmac.compare_digest(expected, guest_mac):
                self._attempts_left -= 1
                left = self._attempts_left
                if left <= 0:
                    self._invalidate()
                raise PairingError(f"неверный код, осталось попыток: {max(left, 0)}", HTTPStatus.FORBIDDEN)
            self._invalidate()  # код одноразовый

        paired = PairedDevice(guest_id, pending.guest_name, pending.keys.shared, guest_cert)
        self.on_paired(paired)
        host_mac = _mac(pending.keys.confirm, b"host", pending.transcript, self.cert_pem)
        return {"cert": self.cert_pem, "mac": host_mac}

    def _active_code(self) -> str | None:
        if self._code is not None and self.clock() > self._expires_at:
            self._invalidate()
        return self._code

    def _invalidate(self) -> None:
        self._code = None
        self._pending.clear()


def pair_with(base_url: str, code: str, device_id: str, device_name: str, cert_pem: str) -> PairedDevice:
    """Сторона-гость: ввести код, показанный на другом устройстве (base_url), и сопрячься."""
    spake = SPAKE2_A(code.strip().encode(), idA=_ID_GUEST, idB=_ID_HOST)
    guest_msg = spake.start()
    timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
    with httpx.Client(base_url=base_url, timeout=timeout, verify=insecure_client_context()) as http:
        reply = _post(http, PAIR_START_PATH, {"device_id": device_id, "name": device_name, "msg": guest_msg.hex()})
        host_id, host_name, host_msg_hex = _require(reply, "device_id", "name", "msg")
        try:
            spake_key = spake.finish(bytes.fromhex(host_msg_hex))
        except Exception:
            raise PairingError("повреждённый ответ при сопряжении") from None
        transcript = _transcript(device_id, host_id, guest_msg, bytes.fromhex(host_msg_hex))
        keys = _derive_keys(spake_key, transcript)

        guest_mac = _mac(keys.confirm, b"guest", transcript, cert_pem)
        reply = _post(http, PAIR_CONFIRM_PATH, {"device_id": device_id, "cert": cert_pem, "mac": guest_mac})
        host_cert, host_mac = _require(reply, "cert", "mac")

    if not hmac.compare_digest(_mac(keys.confirm, b"host", transcript, host_cert), host_mac):
        raise PairingError("другое устройство не подтвердило код — возможна подмена в сети")
    return PairedDevice(host_id, host_name, keys.shared, host_cert)


def _post(http: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = http.post(path, json=payload)
    except httpx.HTTPError as error:
        raise NetworkError(f"не удалось связаться с устройством ({error.__class__.__name__})") from error
    data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
    if response.status_code != 200:
        raise PairingError(data.get("error", f"ошибка {response.status_code}"), HTTPStatus(response.status_code))
    return data


def _require(data: dict[str, Any], *keys: str) -> list[str]:
    """Достать из JSON обязательные строковые поля."""
    try:
        values = [data[key] for key in keys]
    except (KeyError, TypeError):
        raise PairingError("неполное сообщение сопряжения") from None
    if not all(isinstance(value, str) for value in values):
        raise PairingError("неверный формат сообщения сопряжения")
    return values
