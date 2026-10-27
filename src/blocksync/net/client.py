"""Клиент: запросы к серверу другого устройства.

Каждый запрос подписывается общим ключом (см. auth.py).
Все сетевые ошибки превращаются в NetworkError с понятным текстом.
"""

import hashlib
import hmac
import io
import json
import logging
import ssl
import time
import zlib
from collections.abc import Callable, Iterator
from types import TracebackType
from typing import Any, BinaryIO

import httpx

from blocksync.config.sync_state import SyncState, state_from_json
from blocksync.core.apply import CHUNK_SIZE, SyncCancelled
from blocksync.core.manifest import FileEntry
from blocksync.net.auth import sign_request
from blocksync.net.protocol import (
    CHANGED_PATH,
    FRAME_END,
    FRAME_FLAG_CHANGED,
    FRAME_HEADER,
    FRAME_RAW,
    FRAME_ZLIB,
    HEADER_EXPECT,
    HEADER_TRANSFER,
    MAX_FRAME_BYTES,
    TRANSFER_FRAMES,
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
    sync_state_path,
    with_instance,
    expect_header,
    world_from_json,
)

logger = logging.getLogger(__name__)

# Сколько ждать: подключения и каждого следующего куска данных (секунды).
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0


# Если связь прервалась посреди файла: сколько раз и через сколько секунд
# пробовать продолжить с того же места (за это время Wi-Fi обычно успевает переподключиться).
RETRY_DELAYS_SECONDS: tuple[float, ...] = (1, 2, 4, 8, 15)


class NetworkError(Exception):
    """Не удалось связаться с другим устройством или оно ответило ошибкой.

    Текст — для лога. code — вид ошибки: по нему интерфейс покажет текст на выбранном
    языке (ключ error.<code>), params — подробности для этого текста.
    """

    def __init__(self, message: str, code: str | None = None, **params: object) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


class SourceChangedError(NetworkError):
    """Мир на другом устройстве изменился во время передачи (например, его открыли в игре)."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"мир на другом устройстве изменился во время передачи: {detail}", "source_changed")


class _Interrupted(Exception):
    """Передача файла оборвалась, но её можно продолжить с того же места."""

    def __init__(self, message: str, code: str = "net_lost") -> None:
        super().__init__(message)
        self.code = code


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

    def get_sync_state(self, world: str) -> SyncState | None:
        """Каким был мир после последней синхронизации, по сведениям того устройства (None — неизвестно)."""
        try:
            return state_from_json(self._request_json("GET", with_instance(sync_state_path(world), self.instance)))
        except NetworkError as error:
            if error.code == "net_not_found":  # не синхронизировался или там версия 1.0
                return None
            raise
        except (KeyError, TypeError, ValueError):
            logger.warning("Другое устройство прислало неверное состояние мира %s", world)
            return None

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

    def open_file(
        self, world: str, relative_path: str, expected: FileEntry | None = None,
        should_cancel: Callable[[], bool] | None = None, on_wire_bytes: Callable[[int], None] | None = None,
        retry: bool = True,
    ) -> BinaryIO:
        """Начать скачивание файла. Данные читаются кусками через read().

        expected — каким файл был в манифесте: если он с тех пор изменился, будет SourceChangedError.
        Если связь прервётся, скачивание само продолжится с того же места (RETRY_DELAYS_SECONDS).
        on_wire_bytes — сколько байт пришло по сети (меньше размера файла, если данные сжаты).
        retry=False — не ждать и не повторять при обрыве (для мелочей вроде иконок).
        """
        path = with_instance(file_path(world, relative_path), self.instance)
        delays = RETRY_DELAYS_SECONDS if retry else ()
        download = _Download(self, path, expected, should_cancel, on_wire_bytes, delays)
        download.connect()
        return io.BufferedReader(download, CHUNK_SIZE)

    def _open_stream(self, path: str, expected: FileEntry | None, start: int) -> httpx.Response:
        """Отправить запрос файла (с байта start) и вернуть потоковый ответ."""
        request = self._build_request("GET", path, b"")
        request.headers[HEADER_TRANSFER] = TRANSFER_FRAMES
        if expected is not None:
            request.headers[HEADER_EXPECT] = expect_header(expected.size, expected.mtime_ns)
        if start:
            request.headers["Range"] = f"bytes={start}-"
        try:
            response = self._http.send(request, stream=True)
        except httpx.HTTPError as error:
            raise _network_error(error) from error
        if response.status_code not in (200, 206):
            response.read()
            response.close()
            if response.status_code == 409:
                raise SourceChangedError(_error_text(response))
            raise _status_error(response)
        return response

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
            raise _network_error(error) from error
        if response.status_code not in (200, 202):
            raise _status_error(response)
        return response.json()


class RemoteFileSource:
    """Источник файлов для ядра (apply_sync): файлы мира с другого устройства."""

    def __init__(self, client: PeerClient, world: str, should_cancel: Callable[[], bool] | None = None) -> None:
        self.client = client
        self.world = world
        self.should_cancel = should_cancel
        self.wire_bytes = 0  # сколько байт пришло по сети за всё время (со сжатием)

    def open_file(self, entry: FileEntry) -> BinaryIO:
        return self.client.open_file(self.world, entry.path, entry, self.should_cancel, self._count)

    def _count(self, count: int) -> None:
        self.wire_bytes += count


class _Download(io.RawIOBase):
    """Скачивание одного файла: разбирает кадры и продолжает с места обрыва, если связь прервалась."""

    def __init__(
        self, client: PeerClient, path: str, expected: FileEntry | None,
        should_cancel: Callable[[], bool] | None, on_wire_bytes: Callable[[int], None] | None,
        delays: tuple[float, ...],
    ) -> None:
        self._client = client
        self._delays = delays
        self._path = path
        self._expected = expected
        self._should_cancel = should_cancel
        self._on_wire_bytes = on_wire_bytes
        self._offset = 0  # сколько байт файла уже отдано читающему
        self._response: httpx.Response | None = None
        self._pieces: Iterator[bytes] = iter(())
        self._pending = b""
        self._finished = False

    def readable(self) -> bool:
        return True

    def connect(self) -> None:
        """Начать (или продолжить с self._offset) скачивание; при обрыве — повторить с паузами."""
        delays = list(self._delays)
        while True:
            try:
                self._start(self._offset)
                return
            except _Interrupted as error:
                if not delays:
                    raise NetworkError(str(error), error.code) from None
                self._wait(delays.pop(0), error)

    def readinto(self, buffer: Any) -> int:
        delays = list(self._delays)
        while not self._pending:
            if self._finished:
                return 0
            try:
                self._pending = next(self._pieces, b"")
                if not self._pending and not self._finished:
                    raise _Interrupted("ответ закончился раньше файла")
            except _Interrupted as error:
                if not delays:
                    raise NetworkError(str(error), error.code) from None
                self._wait(delays.pop(0), error)
                try:
                    self._start(self._offset)
                except _Interrupted:
                    continue  # не подключились: следующая попытка — в следующем круге
        count = min(len(buffer), len(self._pending))
        buffer[:count] = self._pending[:count]
        self._pending = self._pending[count:]
        self._offset += count
        return count

    def close(self) -> None:
        if not self.closed:
            self._close_response()
        super().close()

    def _start(self, start: int) -> None:
        """Запросить файл с байта start и приготовиться читать ответ."""
        self._close_response()
        self._pending = b""
        self._pieces = iter(())  # если подключиться не выйдет, читать нечего
        try:
            response = self._client._open_stream(self._path, self._expected, start)
        except NetworkError as error:
            if error.code in _RETRYABLE:
                raise _Interrupted(str(error), error.code) from None
            raise
        self._response = response
        if response.headers.get(HEADER_TRANSFER) == TRANSFER_FRAMES:
            self._pieces = self._frames(response)
        else:
            # Старая версия программы на том устройстве: файл как есть, без докачки (всегда с начала).
            skip = start if response.status_code == 200 else 0
            self._pieces = self._raw(response, skip)

    def _raw(self, response: httpx.Response, skip: int) -> Iterator[bytes]:
        for chunk in self._read(response):
            if skip:
                cut = min(skip, len(chunk))
                chunk, skip = chunk[cut:], skip - cut
            if chunk:
                yield chunk
        self._finished = True

    def _frames(self, response: httpx.Response) -> Iterator[bytes]:
        """Разобрать кадры (см. protocol.py) и вернуть куски файла."""
        digest = hashlib.sha256()
        buffer = bytearray()
        for chunk in self._read(response):
            buffer += chunk
            while len(buffer) >= FRAME_HEADER.size:
                kind, length = FRAME_HEADER.unpack_from(buffer)
                if length > MAX_FRAME_BYTES or kind not in (FRAME_RAW, FRAME_ZLIB, FRAME_END):
                    raise _Interrupted("повреждённые данные при передаче")
                if len(buffer) < FRAME_HEADER.size + length:
                    break
                payload = bytes(buffer[FRAME_HEADER.size:FRAME_HEADER.size + length])
                del buffer[:FRAME_HEADER.size + length]
                if kind == FRAME_END:
                    self._check_end(payload, digest.digest())
                    self._finished = True
                    return
                data = _inflate(payload) if kind == FRAME_ZLIB else payload
                digest.update(data)
                yield data

    def _check_end(self, payload: bytes, actual: bytes) -> None:
        if len(payload) != 33 or not hmac.compare_digest(payload[:32], actual):
            # Данные уже записаны во временную папку — продолжить нельзя, только начать заново.
            raise NetworkError("контрольная сумма файла не совпала", "net_corrupted")
        if payload[32] & FRAME_FLAG_CHANGED:
            raise SourceChangedError(f"файл {self._path} менялся, пока передавался")

    def _read(self, response: httpx.Response) -> Iterator[bytes]:
        """Куски ответа как пришли по сети; обрыв связи превращается в _Interrupted."""
        # Без размера куска: httpx отдаёт данные сразу, как пришли, а не копит их до 1 МБ.
        chunks = response.iter_raw()
        while True:
            try:
                chunk = next(chunks, None)
            except httpx.HTTPError as error:
                network_error = _network_error(error)
                raise _Interrupted(str(network_error), network_error.code or "net_lost") from None
            if chunk is None:
                return
            if self._on_wire_bytes is not None:
                self._on_wire_bytes(len(chunk))
            yield chunk

    def _wait(self, seconds: float, error: Exception) -> None:
        logger.warning("Связь прервалась (%s), продолжу %s через %s с", error, self._path, seconds)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._should_cancel is not None and self._should_cancel():
                raise SyncCancelled()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _close_response(self) -> None:
        if self._response is not None:
            self._response.close()
            self._response = None


def _inflate(payload: bytes) -> bytes:
    """Распаковать сжатый кусок (не больше MAX_FRAME_BYTES — защита от «zip-бомбы»)."""
    inflater = zlib.decompressobj()
    try:
        data = inflater.decompress(payload, MAX_FRAME_BYTES)
    except zlib.error:
        raise _Interrupted("повреждённые сжатые данные") from None
    if inflater.unconsumed_tail or not inflater.eof:
        raise _Interrupted("повреждённые сжатые данные")
    return data


# Ошибки, после которых имеет смысл подождать и попробовать снова.
_RETRYABLE = frozenset({"net_connect", "net_timeout", "net_lost"})


def _network_error(error: httpx.HTTPError) -> NetworkError:
    """Понятная ошибка для типичных сетевых сбоев."""
    if isinstance(error, httpx.ConnectError) and "CERTIFICATE" in str(error).upper():
        return NetworkError("сертификат другого устройства не совпадает с сохранённым при сопряжении", "net_certificate")
    if isinstance(error, httpx.ConnectError):
        return NetworkError("не удалось подключиться к другому устройству (оно выключено или не в сети?)", "net_connect")
    if isinstance(error, httpx.TimeoutException):
        return NetworkError("другое устройство не отвечает", "net_timeout")
    return NetworkError(f"связь с другим устройством прервалась ({error.__class__.__name__})", "net_lost")


def _status_error(response: httpx.Response) -> NetworkError:
    """Другое устройство ответило ошибкой."""
    codes = {401: "net_unauthorized", 404: "net_not_found"}
    return NetworkError(_error_text(response), codes.get(response.status_code, "net_status"), status=response.status_code)


def _error_text(response: httpx.Response) -> str:
    try:
        message = response.json().get("error", "")
    except (ValueError, json.JSONDecodeError):
        message = ""
    return f"другое устройство ответило ошибкой {response.status_code}: {message}".rstrip(": ")
