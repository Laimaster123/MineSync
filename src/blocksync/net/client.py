"""Клиент: запросы к серверу другого устройства.

Каждый запрос подписывается общим ключом (см. auth.py).
Все сетевые ошибки превращаются в NetworkError с понятным текстом.
"""

import io
import json
import ssl
from collections.abc import Iterator
from types import TracebackType
from typing import Any, BinaryIO

import httpx

from blocksync.core.apply import CHUNK_SIZE
from blocksync.net.auth import sign_request
from blocksync.net.protocol import (
    CHANGED_PATH,
    INSTANCE_LINKS_PATH,
    INSTANCES_PATH,
    SYNC_REQUESTS_PATH,
    UNPAIR_PATH,
    WORLDS_PATH,
    RemoteInstance,
    RemoteManifest,
    RemoteWorld,
    file_path,
    manifest_from_json,
    manifest_path,
    with_instance,
    world_from_json,
)

# Сколько ждать: подключения и каждого следующего куска данных (секунды).
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0


class NetworkError(Exception):
    """Не удалось связаться с другим устройством или оно ответило ошибкой."""


class PeerClient:
    """Клиент для одного сопряжённого устройства.

    base_url: адрес сервера, например "https://192.168.1.20:8001".
    device_id, key: ID этого устройства и общий ключ пары.
    ssl_context: настройки HTTPS с сертификатом второго устройства (tls.client_context).
    instance: сборка на втором устройстве, с мирами которой работаем (None — папка по умолчанию).
    """

    def __init__(
        self, base_url: str, device_id: str, key: bytes, ssl_context: ssl.SSLContext | None = None,
        instance: str | None = None,
    ) -> None:
        self.device_id = device_id
        self.instance = instance
        self._key = key
        timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
        verify: ssl.SSLContext | bool = ssl_context if ssl_context is not None else True
        self._http = httpx.Client(base_url=base_url, timeout=timeout, verify=verify)

    def __enter__(self) -> "PeerClient":
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def list_instances(self) -> list[RemoteInstance]:
        data = self._request_json("GET", INSTANCES_PATH)
        return [RemoteInstance(item["id"], item["name"]) for item in data["instances"]]

    def list_worlds(self) -> list[RemoteWorld]:
        data = self._request_json("GET", with_instance(WORLDS_PATH, self.instance))
        return [world_from_json(item) for item in data["worlds"]]

    def get_manifest(self, world: str) -> RemoteManifest:
        return manifest_from_json(self._request_json("GET", with_instance(manifest_path(world), self.instance)))

    def notify_changed(self) -> None:
        """Сообщить второму устройству, что здесь изменились миры (пусть обновит список)."""
        self._request_json("POST", CHANGED_PATH, {})

    def send_instance_link(self, their_instance: str, our_instance: str | None) -> None:
        """Сообщить, что сборка their_instance на том устройстве связана с нашей our_instance (None — связь удалена)."""
        self._request_json("POST", INSTANCE_LINKS_PATH, {"instance": their_instance, "peer_instance": our_instance})

    def unpair(self) -> None:
        """Сообщить другому устройству, что пара удалена."""
        self._request_json("POST", UNPAIR_PATH, {})

    def request_sync(self, world: str, our_instance: str | None = None) -> None:
        """Попросить другое устройство забрать мир у этого.

        our_instance — наша сборка, откуда брать мир; положить его нужно в сборку self.instance.
        """
        payload = {"world": world, "instance": our_instance, "target_instance": self.instance}
        self._request_json("POST", SYNC_REQUESTS_PATH, payload)

    def open_file(self, world: str, relative_path: str) -> BinaryIO:
        """Начать скачивание файла. Данные читаются кусками через read()."""
        path = with_instance(file_path(world, relative_path), self.instance)
        request = self._build_request("GET", path, b"")
        try:
            response = self._http.send(request, stream=True)
        except httpx.HTTPError as error:
            raise NetworkError(_describe(error)) from error
        if response.status_code != 200:
            response.read()
            response.close()
            raise NetworkError(_error_text(response))
        return io.BufferedReader(_ResponseStream(response), CHUNK_SIZE)

    def _build_request(self, method: str, path: str, body: bytes) -> httpx.Request:
        headers = sign_request(self.device_id, self._key, method, path, body)
        if body:
            headers["Content-Type"] = "application/json"
        return self._http.build_request(method, path, content=body, headers=headers)

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        try:
            response = self._http.send(self._build_request(method, path, body))
        except httpx.HTTPError as error:
            raise NetworkError(_describe(error)) from error
        if response.status_code not in (200, 202):
            raise NetworkError(_error_text(response))
        return response.json()


class RemoteFileSource:
    """Источник файлов для ядра (apply_sync): файлы мира с другого устройства."""

    def __init__(self, client: PeerClient, world: str) -> None:
        self.client = client
        self.world = world

    def open_file(self, relative_path: str) -> BinaryIO:
        return self.client.open_file(self.world, relative_path)


class _ResponseStream(io.RawIOBase):
    """Превращает потоковый ответ httpx в обычный «файл» с методом read()."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self._chunks: Iterator[bytes] = response.iter_raw(CHUNK_SIZE)
        self._pending = b""

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while not self._pending:
            try:
                chunk = next(self._chunks, None)
            except httpx.HTTPError as error:
                raise NetworkError(_describe(error)) from error
            if chunk is None:
                return 0  # конец файла
            self._pending = chunk
        count = min(len(buffer), len(self._pending))
        buffer[:count] = self._pending[:count]
        self._pending = self._pending[count:]
        return count

    def close(self) -> None:
        if not self.closed:
            self._response.close()
        super().close()


def _describe(error: httpx.HTTPError) -> str:
    """Понятный текст для типичных сетевых ошибок."""
    if isinstance(error, httpx.ConnectError) and "CERTIFICATE" in str(error).upper():
        return "сертификат другого устройства не совпадает с сохранённым при сопряжении"
    if isinstance(error, httpx.ConnectError):
        return "не удалось подключиться к другому устройству (оно выключено или не в сети?)"
    if isinstance(error, httpx.TimeoutException):
        return "другое устройство не отвечает"
    return f"связь с другим устройством прервалась ({error.__class__.__name__})"


def _error_text(response: httpx.Response) -> str:
    try:
        message = response.json().get("error", "")
    except (ValueError, json.JSONDecodeError):
        message = ""
    return f"другое устройство ответило ошибкой {response.status_code}: {message}".rstrip(": ")
