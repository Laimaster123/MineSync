"""HTTP-сервер, который отдаёт миры этого устройства сопряжённому устройству.

Сервер работает в фоновом потоке и каждый запрос обрабатывает в своём потоке
(ThreadingHTTPServer), поэтому не мешает интерфейсу и может одновременно
отдавать несколько файлов.
"""

import json
import logging
import os
import re
import ssl
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import parse_qs, unquote, urlsplit

from blocksync.core.apply import CHUNK_SIZE
from blocksync.core.level_dat import LevelDatError, read_level_info
from blocksync.core.lock import is_world_locked
from blocksync.core.manifest import IGNORED_NAMES, build_manifest, find_on_disk
from blocksync.core.world import LEVEL_DAT_NAME, is_safe_world_name, is_world_dir, list_worlds
from blocksync.net.auth import AuthError, RequestVerifier
from blocksync.net.pairing import PairingError, PairingHost
from blocksync.net.protocol import (
    CHANGED_PATH,
    INSTANCE_LINKS_PATH,
    INSTANCES_PATH,
    PAIR_CONFIRM_PATH,
    PAIR_START_PATH,
    SYNC_REQUESTS_PATH,
    UNPAIR_PATH,
    WORLDS_PATH,
    SyncRequest,
    manifest_to_json,
    world_to_json,
)

logger = logging.getLogger(__name__)

# Самый большой допустимый размер тела запроса (у нас только короткий JSON).
MAX_BODY_BYTES = 64 * 1024

# Регулярные выражения для разбора адреса: (?P<world>...) — именованная часть.
_MANIFEST_RE = re.compile(rf"^{WORLDS_PATH}/(?P<world>[^/]+)/manifest$")
_FILE_RE = re.compile(rf"^{WORLDS_PATH}/(?P<world>[^/]+)/files/(?P<path>.+)$")

# Функция, которую сервер вызывает на просьбу «забери мир»: (ID устройства, просьба).
SyncRequestHandler = Callable[[str, SyncRequest], None]
# Сообщение о связи сборок: (ID устройства, наша сборка, сборка того устройства или None).
InstanceLinkHandler = Callable[[str, str, str | None], None]


class RequestError(Exception):
    """Ошибка запроса, о которой нужно сообщить клиенту HTTP-кодом."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


class SyncServer:
    """Сервер одного устройства.

    saves_dir: папка с мирами, которые сервер отдаёт.
    verifier: проверяет подписи; запросы от несопряжённых устройств отклоняются.
    on_sync_request: что сделать, когда второе устройство просит забрать мир.
    on_unpair: что сделать, когда второе устройство сообщает, что забыло пару.
    pairing: сопряжение по коду; None — сервер сопряжение не принимает.
    world_notes: описания миров, которые видит второе устройство ({папка: текст}).
    instances: список сборок [(ID, название)]; resolve_instance: папка saves сборки по ID.
    on_instance_link: второе устройство сообщило о связи сборок.
    """

    def __init__(
        self,
        saves_dir: Path,
        verifier: RequestVerifier,
        host: str = "127.0.0.1",
        port: int = 0,
        on_sync_request: SyncRequestHandler | None = None,
        ssl_context: ssl.SSLContext | None = None,
        on_unpair: Callable[[str], None] | None = None,
        pairing: PairingHost | None = None,
        world_notes: Callable[[], dict[str, str]] | None = None,
        instances: Callable[[], list[tuple[str, str]]] | None = None,
        resolve_instance: Callable[[str], Path | None] | None = None,
        on_instance_link: InstanceLinkHandler | None = None,
        on_peer_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.saves_dir = saves_dir
        self.world_notes = world_notes
        self.instances = instances
        self.resolve_instance = resolve_instance
        self.on_instance_link = on_instance_link
        self.on_peer_changed = on_peer_changed
        self.verifier = verifier
        self.on_sync_request = on_sync_request
        self.on_unpair = on_unpair
        self.pairing = pairing
        # port=0 значит «выбери любой свободный порт».
        self._httpd = _HttpServer((host, port), _Handler, self)
        if ssl_context is not None:
            # Включаем HTTPS. Рукопожатие TLS выполняется уже в потоке запроса,
            # чтобы медленный клиент не задерживал приём остальных соединений.
            self._httpd.socket = ssl_context.wrap_socket(
                self._httpd.socket, server_side=True, do_handshake_on_connect=False
            )
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def start(self) -> None:
        # poll_interval: как часто сервер проверяет, не пора ли остановиться.
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.1}, name="sync-server", daemon=True
        )
        self._thread.start()
        logger.info("Сервер запущен на порту %s", self.port)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join()
        logger.info("Сервер остановлен")

    # --- Поиск миров и файлов с защитой от выхода за пределы папки saves ---

    def saves_for(self, instance: str | None) -> Path:
        """Папка saves сборки instance (None — папка по умолчанию)."""
        if instance is None:
            return self.saves_dir
        path = self.resolve_instance(instance) if self.resolve_instance is not None else None
        if path is None:
            raise RequestError(HTTPStatus.NOT_FOUND, f"сборка не найдена: {instance}")
        return path

    def world_dir(self, world: str, saves_dir: Path | None = None) -> Path:
        """Папка мира по имени из адреса. Имя не может указывать за пределы saves."""
        if not is_safe_world_name(world):
            raise RequestError(HTTPStatus.BAD_REQUEST, "неверное имя мира")
        path = (saves_dir or self.saves_dir) / world
        if not is_world_dir(path):
            raise RequestError(HTTPStatus.NOT_FOUND, "мир не найден")
        return path

    def world_file(self, world: str, relative_path: str, saves_dir: Path | None = None) -> Path:
        """Файл внутри мира. Пути с ".." и ссылки наружу отклоняются."""
        world_dir = self.world_dir(world, saves_dir)
        parts = relative_path.split("/")
        if any(part in ("", ".", "..") for part in parts) or "\\" in relative_path:
            raise RequestError(HTTPStatus.BAD_REQUEST, "неверный путь файла")
        if parts[-1] in IGNORED_NAMES:
            raise RequestError(HTTPStatus.FORBIDDEN, "этот файл не передаётся")
        path = find_on_disk(world_dir, relative_path)
        if path.is_symlink() or not path.is_file():
            raise RequestError(HTTPStatus.NOT_FOUND, "файл не найден")
        if not path.resolve().is_relative_to(world_dir.resolve()):
            raise RequestError(HTTPStatus.FORBIDDEN, "файл вне папки мира")
        return path


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], app: SyncServer) -> None:
        super().__init__(address, handler)
        self.app = app

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Обрыв соединения — обычное дело в Wi-Fi, пишем в лог вместо вывода трейсбека.
        logger.warning("Ошибка при обработке запроса от %s", client_address, exc_info=True)


class _Handler(BaseHTTPRequestHandler):
    """Обрабатывает один HTTP-запрос."""

    server: _HttpServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)

    def _handle(self, method: str) -> None:
        app = self.server.app
        try:
            body = self._read_body()
            path = unquote(urlsplit(self.path).path)
            if method == "POST" and path in (PAIR_START_PATH, PAIR_CONFIRM_PATH):
                # Сопряжение идёт без подписи: общего ключа ещё нет.
                self._route_pairing(app, path, body)
                return
            device_id = app.verifier.verify(method, self.path, self.headers, body)
            if method == "GET":
                instance = parse_qs(urlsplit(self.path).query).get("instance", [None])[0]
                self._route_get(app, path, app.saves_for(instance))
            else:
                self._route_post(app, path, device_id, body)
        except AuthError as error:
            logger.warning("Отклонён запрос от %s: %s", self.address_string(), error)
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
        except (RequestError, PairingError) as error:
            self._send_json(error.status, {"error": str(error)})

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            # Тело не прочитано, поэтому это соединение дальше использовать нельзя.
            self.close_connection = True
            raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "слишком большой запрос")
        return self.rfile.read(length) if length else b""

    def _route_get(self, app: SyncServer, path: str, saves_dir: Path) -> None:
        if path == INSTANCES_PATH:
            items = app.instances() if app.instances is not None else []
            self._send_json(HTTPStatus.OK, {"instances": [{"id": i, "name": n} for i, n in items]})
        elif path == WORLDS_PATH:
            worlds = list_worlds(saves_dir) if saves_dir.is_dir() else []
            # Описания привязаны к папке мира; одинаковые папки в разных сборках — редкость.
            notes = app.world_notes() if app.world_notes is not None else {}
            self._send_json(HTTPStatus.OK, {"worlds": [world_to_json(w, notes.get(w.folder_name)) for w in worlds]})
        elif match := _MANIFEST_RE.match(path):
            self._send_manifest(app.world_dir(match["world"], saves_dir))
        elif match := _FILE_RE.match(path):
            self._send_file(app.world_file(match["world"], match["path"], saves_dir))
        else:
            raise RequestError(HTTPStatus.NOT_FOUND, "неизвестный адрес")

    def _route_pairing(self, app: SyncServer, path: str, body: bytes) -> None:
        if app.pairing is None:
            raise RequestError(HTTPStatus.SERVICE_UNAVAILABLE, "сопряжение недоступно")
        data = _parse_json(body)
        if path == PAIR_START_PATH:
            reply = app.pairing.handle_start(data)
        else:
            reply = app.pairing.handle_confirm(data)
        self._send_json(HTTPStatus.OK, reply)

    def _route_post(self, app: SyncServer, path: str, device_id: str, body: bytes) -> None:
        if path == UNPAIR_PATH:
            if app.on_unpair is not None:
                app.on_unpair(device_id)
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == CHANGED_PATH:
            if app.on_peer_changed is not None:
                app.on_peer_changed(device_id)
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == INSTANCE_LINKS_PATH:
            data = _parse_json(body)
            instance, peer_instance = data.get("instance"), data.get("peer_instance")
            if not isinstance(instance, str) or not isinstance(peer_instance, (str, type(None))):
                raise RequestError(HTTPStatus.BAD_REQUEST, "неверная связь сборок")
            app.saves_for(instance)  # такая сборка должна быть на этом устройстве
            if app.on_instance_link is not None:
                app.on_instance_link(device_id, instance, peer_instance)
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path != SYNC_REQUESTS_PATH:
            raise RequestError(HTTPStatus.NOT_FOUND, "неизвестный адрес")
        data = _parse_json(body)
        world, instance, target = data.get("world"), data.get("instance"), data.get("target_instance")
        if not isinstance(world, str) or not is_safe_world_name(world):
            raise RequestError(HTTPStatus.BAD_REQUEST, "неверное имя мира")
        if not isinstance(instance, (str, type(None))) or not isinstance(target, (str, type(None))):
            raise RequestError(HTTPStatus.BAD_REQUEST, "неверная сборка")
        if target is not None:
            app.saves_for(target)  # сборка, куда положить мир, должна существовать здесь
        if app.on_sync_request is None:
            raise RequestError(HTTPStatus.SERVICE_UNAVAILABLE, "устройство не может принять мир")
        app.on_sync_request(device_id, SyncRequest(world, instance, target))
        self._send_json(HTTPStatus.ACCEPTED, {"status": "accepted"})

    def _send_manifest(self, world_dir: Path) -> None:
        try:
            data_version = read_level_info(world_dir / LEVEL_DAT_NAME).data_version
        except LevelDatError:
            data_version = None
        payload = manifest_to_json(build_manifest(world_dir), data_version, is_world_locked(world_dir))
        self._send_json(HTTPStatus.OK, payload)

    def _send_file(self, path: Path) -> None:
        with open(path, "rb") as file:
            size = os.fstat(file.fileno()).st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            self._copy_file(file, size)

    def _copy_file(self, file: BinaryIO, size: int) -> None:
        """Отправить файл кусками, не загружая его в память целиком."""
        remaining = size
        while remaining > 0:
            chunk = file.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            self.wfile.write(chunk)
            remaining -= len(chunk)

    def _send_json(self, status: HTTPStatus, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _parse_json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError:
        raise RequestError(HTTPStatus.BAD_REQUEST, "тело запроса — не JSON") from None
    if not isinstance(data, dict):
        raise RequestError(HTTPStatus.BAD_REQUEST, "ожидался JSON-объект")
    return data
