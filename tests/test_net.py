"""Передача миров по сети между двумя «устройствами» на одном компьютере.

«ПК» запускает сервер со своей папкой saves, «Мак» подключается к нему клиентом
и забирает мир в свою папку.
"""

import os
import socket
import sys
import threading
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

import httpx
import pytest

from blocksync.config.sync_state import SyncState
from blocksync.core.compare import compare
from blocksync.core.lock import WorldLockedError
from blocksync.core.manifest import FileEntry, build_manifest
from blocksync.core.staging import staging_root
from blocksync.net import server as server_module
from blocksync.net.auth import RequestVerifier
from blocksync.net.client import NetworkError, PeerClient, RemoteFileSource, SourceChangedError
from blocksync.net.protocol import SyncRequest
from blocksync.net.pull import pull_world
from blocksync.net.server import RequestError, SyncServer
from helpers import BASE_TIME, hold_lock, make_level_dat, make_world, read_world, write_file, leftovers

KEY = b"0123456789abcdef0123456789abcdef"

WORLD_FILES = {
    "region/r.0.0.mca": bytes(range(256)) * 10_000,  # ~2.5 МБ: передаётся несколькими кусками
    "data/мой файл.dat": b"cyrillic",
    unicodedata.normalize("NFD", "data/ёжик й.dat"): b"nfd",
    "icon.png": b"png",
}


@dataclass
class Devices:
    pc_saves: Path
    mac_saves: Path
    mac_tmp: Path
    mac_backups: Path
    server: SyncServer
    client: PeerClient

    def pull(self, world: str) -> None:
        """Мак забирает мир с ПК."""
        pull_world(self.client, world, self.mac_saves, self.mac_tmp, self.mac_backups, keep_backups=3)


@pytest.fixture
def devices(tmp_path: Path) -> Iterator[Devices]:
    pc_saves = tmp_path / "pc" / "saves"
    world = make_world(pc_saves / "Мой мир", WORLD_FILES)
    make_level_dat(world, level_name="Мой мир", data_version=5023)

    verifier = RequestVerifier(lambda device: KEY if device == "mac" else None)
    server = SyncServer(pc_saves, verifier)
    server.start()
    client = PeerClient(f"http://127.0.0.1:{server.port}", "mac", KEY)
    try:
        yield Devices(
            pc_saves, tmp_path / "mac" / "saves", tmp_path / "mac" / "tmp",
            tmp_path / "mac" / "backups", server, client,
        )
    finally:
        client.close()
        server.stop()


def test_list_worlds(devices: Devices) -> None:
    [world] = devices.client.list_worlds()
    assert world.folder_name == "Мой мир"
    assert world.data_version == 5023
    assert world.has_icon and not world.is_locked


def test_manifest_matches_local(devices: Devices) -> None:
    remote = devices.client.get_manifest("Мой мир")
    assert remote.manifest == build_manifest(devices.pc_saves / "Мой мир")
    assert remote.data_version == 5023


def test_world_is_transferred(devices: Devices) -> None:
    devices.pull("Мой мир")

    pc_world, mac_world = devices.pc_saves / "Мой мир", devices.mac_saves / "Мой мир"
    assert read_world(mac_world) == read_world(pc_world)
    # Повторное сравнение по сети не находит различий.
    assert not compare(devices.client.get_manifest("Мой мир").manifest, build_manifest(mac_world)).has_changes


def test_only_changed_file_is_transferred_again(devices: Devices) -> None:
    devices.pull("Мой мир")
    write_file(devices.pc_saves / "Мой мир" / "data/мой файл.dat", b"changed", mtime=BASE_TIME + 100)

    remote = devices.client.get_manifest("Мой мир")
    plan = compare(remote.manifest, build_manifest(devices.mac_saves / "Мой мир"))

    assert [e.path for e in plan.to_copy] == ["data/мой файл.dat"]


def test_locked_world_reported(devices: Devices) -> None:
    lock_file = devices.pc_saves / "Мой мир" / "session.lock"
    write_file(lock_file, b"lock")
    with hold_lock(lock_file):
        assert devices.client.get_manifest("Мой мир").is_locked
        with pytest.raises(WorldLockedError):
            devices.pull("Мой мир")


def test_broken_connection_leaves_world_untouched(
    devices: Devices, monkeypatch: pytest.MonkeyPatch
) -> None:
    devices.pull("Мой мир")
    mac_world = devices.mac_saves / "Мой мир"
    write_file(devices.pc_saves / "Мой мир" / "region/r.0.0.mca", b"x" * 3_000_000, mtime=BASE_TIME + 100)
    before = read_world(mac_world)

    def send_half_and_crash(handler: object, file: BinaryIO, size: int) -> None:
        handler.wfile.write(file.read(size // 2))  # type: ignore[attr-defined]
        raise ConnectionResetError("сервер упал посреди передачи")

    monkeypatch.setattr(server_module._Handler, "_copy_file", send_half_and_crash)

    with pytest.raises(NetworkError):
        devices.pull("Мой мир")

    assert read_world(mac_world) == before
    assert leftovers(devices.mac_tmp) == []


def test_sync_request_calls_handler(devices: Devices) -> None:
    received: list[tuple[str, SyncRequest]] = []
    devices.server.on_sync_request = lambda device, request: received.append((device, request))

    devices.client.request_sync("Мой мир")

    assert received == [("mac", SyncRequest("Мой мир", None, None))]


def test_instances(devices: Devices, tmp_path: Path) -> None:
    """Миры разных сборок: адрес с ?instance=..., неизвестная сборка, связи сборок, просьба со сборками."""
    modded = tmp_path / "pc" / "modded saves"
    make_level_dat(make_world(modded / "Модовый мир", {"a": b"1"}), level_name="Модовый мир")
    folders = {"prism:26.3": devices.pc_saves, "prism:modpack": modded}
    devices.server.instances = lambda: [("prism:26.3", "26.3"), ("prism:modpack", "Мой мод")]
    devices.server.resolve_instance = folders.get
    links: list[tuple[str, str, str | None]] = []
    devices.server.on_instance_link = lambda device, ours, theirs: links.append((device, ours, theirs))
    requests: list[SyncRequest] = []
    devices.server.on_sync_request = lambda device, request: requests.append(request)

    assert [(i.id, i.name) for i in devices.client.list_instances()] == [("prism:26.3", "26.3"), ("prism:modpack", "Мой мод")]
    devices.client.instance = "prism:modpack"
    assert [w.folder_name for w in devices.client.list_worlds()] == ["Модовый мир"]
    assert "a" in devices.client.get_manifest("Модовый мир").manifest
    with devices.client.open_file("Модовый мир", "a") as stream:
        assert stream.read() == b"1"

    devices.client.request_sync("Мир с Мака", our_instance="prism:mac")
    assert requests == [SyncRequest("Мир с Мака", "prism:mac", "prism:modpack")]

    devices.client.send_instance_link("prism:modpack", "prism:mac")
    devices.client.send_instance_link("prism:modpack", None)
    assert links == [("mac", "prism:modpack", "prism:mac"), ("mac", "prism:modpack", None)]

    devices.client.instance = "prism:нет такой"
    with pytest.raises(NetworkError, match="404"):
        devices.client.list_worlds()
    with pytest.raises(NetworkError, match="404"):
        devices.client.request_sync("Мир")


def test_unpaired_device_is_rejected(devices: Devices) -> None:
    with PeerClient(f"http://127.0.0.1:{devices.server.port}", "stranger", KEY) as stranger:
        with pytest.raises(NetworkError, match="401"):
            stranger.list_worlds()


def test_wrong_key_is_rejected(devices: Devices) -> None:
    with PeerClient(f"http://127.0.0.1:{devices.server.port}", "mac", b"x" * 32) as wrong:
        with pytest.raises(NetworkError, match="401"):
            wrong.list_worlds()


def test_unsigned_request_is_rejected(devices: Devices) -> None:
    response = httpx.get(f"http://127.0.0.1:{devices.server.port}/api/v1/worlds")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "world, path",
    [
        ("Мой мир", "../../../etc/passwd"),
        ("Мой мир", "data/../../Мой мир/level.dat"),
        ("..", "pc/saves/Мой мир/level.dat"),
        ("Мой мир", "session.lock"),
        ("Нет такого", "level.dat"),
    ],
)
def test_paths_outside_world_are_rejected(devices: Devices, world: str, path: str) -> None:
    write_file(devices.pc_saves / "Мой мир" / "session.lock", b"lock")
    with pytest.raises(NetworkError):
        devices.client.open_file(world, path).read()


def test_several_downloads_at_once(devices: Devices) -> None:
    """Сервер обслуживает несколько запросов параллельно."""
    results: list[bytes] = []

    def download() -> None:
        with PeerClient(f"http://127.0.0.1:{devices.server.port}", "mac", KEY) as client:
            with client.open_file("Мой мир", "region/r.0.0.mca") as file:
                results.append(file.read())

    threads = [threading.Thread(target=download) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [WORLD_FILES["region/r.0.0.mca"]] * 3


@pytest.mark.parametrize(
    "world, path",
    [
        ("Мой мир", "../../secret.txt"),
        ("Мой мир", "data/../../secret.txt"),
        ("..", "secret.txt"),
        ("Мой мир/../..", "secret.txt"),
        ("Мой мир", "/etc/passwd"),
        ("Мой мир", "data\\..\\..\\secret.txt"),
    ],
)
def test_server_rejects_paths_outside_world(devices: Devices, world: str, path: str) -> None:
    # Проверяем защиту напрямую, без сети: httpx мог бы сам убрать ".." из адреса.
    write_file(devices.pc_saves.parent / "secret.txt", b"secret")
    with pytest.raises(RequestError):
        devices.server.world_file(world, path)


@pytest.mark.skipif(sys.platform == "win32", reason="символические ссылки на Windows требуют прав")
def test_server_rejects_symlink_outside_world(devices: Devices) -> None:
    secret = devices.pc_saves.parent / "secret.txt"
    write_file(secret, b"secret")
    os.symlink(secret, devices.pc_saves / "Мой мир" / "link.dat")
    with pytest.raises(RequestError):
        devices.server.world_file("Мой мир", "link.dat")


@pytest.mark.parametrize("length", ["-1", "abc"])
def test_bad_content_length_is_rejected(devices: Devices, length: str) -> None:
    """Неверная длина тела — сразу ошибка 400, а не чтение «до конца соединения»."""
    with socket.create_connection(("127.0.0.1", devices.server.port), timeout=5) as connection:
        connection.sendall(f"POST /api/v1/pair/start HTTP/1.1\r\nHost: x\r\nContent-Length: {length}\r\n\r\n".encode())
        connection.sendall(b"A" * 100_000)
        assert connection.recv(64).startswith(b"HTTP/1.1 400")


def test_silent_connection_is_closed(devices: Devices, monkeypatch: pytest.MonkeyPatch) -> None:
    """Соединение, по которому ничего не присылают, сервер закрывает сам."""
    monkeypatch.setattr(server_module._Handler, "timeout", 0.3)
    with socket.create_connection(("127.0.0.1", devices.server.port), timeout=5) as connection:
        assert connection.recv(64) == b""  # сервер закрыл соединение


# --- Передача файлов: сжатие, докачка, проверка, что источник не менялся ---


def _record_ranges(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Запоминать заголовок Range каждого запроса файла."""
    ranges: list[str | None] = []
    original = server_module._Handler._send_file

    def send_file(handler: server_module._Handler, path: Path) -> None:
        ranges.append(handler.headers.get("Range"))
        original(handler, path)

    monkeypatch.setattr(server_module._Handler, "_send_file", send_file)
    return ranges


def _crash_first_big_transfer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Первая передача большого файла обрывается на середине (как при пропаже Wi-Fi)."""
    original = server_module._Handler._copy_file
    crashed: list[bool] = []

    def copy_file(handler: server_module._Handler, file: BinaryIO, size: int) -> None:
        if crashed or size < 1_000_000:
            original(handler, file, size)
            return
        crashed.append(True)
        original(handler, file, size // 2)
        raise ConnectionResetError("связь пропала посреди передачи")

    monkeypatch.setattr(server_module._Handler, "_copy_file", copy_file)


def test_compressible_data_is_sent_compressed(devices: Devices, tmp_path: Path) -> None:
    content = b"minecraft " * 500_000  # 5 МБ, хорошо сжимается
    write_file(devices.pc_saves / "Мой мир" / "stats/big.json", content)
    source = RemoteFileSource(devices.client, "Мой мир")

    with source.open_file(FileEntry("stats/big.json", len(content), BASE_TIME * 10**9)) as stream:
        assert stream.read() == content

    assert source.wire_bytes < len(content) / 10


def test_pull_reports_bytes_transferred(devices: Devices) -> None:
    result = pull_world(devices.client, "Мой мир", devices.mac_saves, devices.mac_tmp, devices.mac_backups, 3)
    assert read_world(devices.mac_saves / "Мой мир") == read_world(devices.pc_saves / "Мой мир")
    assert result.bytes_transferred is not None and 0 < result.bytes_transferred < result.bytes_copied


def test_interrupted_transfer_resumes_from_same_place(devices: Devices, monkeypatch: pytest.MonkeyPatch) -> None:
    _crash_first_big_transfer(monkeypatch)
    ranges = _record_ranges(monkeypatch)

    devices.pull("Мой мир")

    assert read_world(devices.mac_saves / "Мой мир") == read_world(devices.pc_saves / "Мой мир")
    resumed = [r for r in ranges if r is not None]
    assert len(resumed) == 1 and resumed[0].startswith("bytes=") and resumed[0] != "bytes=0-"


def test_resume_works_with_old_version_on_other_computer(devices: Devices, monkeypatch: pytest.MonkeyPatch) -> None:
    """Версия 1.0 не знает ни кадров, ни Range: файл приходит целиком с начала, лишнее пропускаем."""
    monkeypatch.setattr(server_module, "TRANSFER_FRAMES", "unknown")
    monkeypatch.setattr(server_module, "_range_start", lambda header, size: 0)
    _crash_first_big_transfer(monkeypatch)

    devices.pull("Мой мир")

    assert read_world(devices.mac_saves / "Мой мир") == read_world(devices.pc_saves / "Мой мир")


def test_file_changed_since_manifest_is_rejected(devices: Devices) -> None:
    entry = devices.client.get_manifest("Мой мир").manifest["region/r.0.0.mca"]
    write_file(devices.pc_saves / "Мой мир" / "region/r.0.0.mca", b"new", mtime=BASE_TIME + 50)

    with pytest.raises(SourceChangedError):
        devices.client.open_file("Мой мир", entry.path, entry)


def test_file_changed_while_sending_is_rejected(devices: Devices, monkeypatch: pytest.MonkeyPatch) -> None:
    original = server_module._Handler._copy_file
    region = devices.pc_saves / "Мой мир" / "region/r.0.0.mca"

    def copy_and_touch(handler: server_module._Handler, file: BinaryIO, size: int) -> None:
        original(handler, file, size)
        if size > 1_000_000:
            os.utime(region, (BASE_TIME + 60, BASE_TIME + 60))  # игра сохранила файл, пока он передавался

    monkeypatch.setattr(server_module._Handler, "_copy_file", copy_and_touch)

    with pytest.raises(SourceChangedError):
        devices.pull("Мой мир")
    assert not (devices.mac_saves / "Мой мир").exists()


def test_world_changed_during_pull_is_not_applied(devices: Devices, monkeypatch: pytest.MonkeyPatch) -> None:
    """Во время передачи на том компьютере появился новый файл: скачанное в мир не ставится."""
    devices.pull("Мой мир")
    write_file(devices.pc_saves / "Мой мир" / "icon.png", b"new icon", mtime=BASE_TIME + 100)
    before = read_world(devices.mac_saves / "Мой мир")
    original = server_module._Handler._send_file

    def send_and_add(handler: server_module._Handler, path: Path) -> None:
        original(handler, path)
        write_file(devices.pc_saves / "Мой мир" / "region/r.1.0.mca", b"new region")

    monkeypatch.setattr(server_module._Handler, "_send_file", send_and_add)

    with pytest.raises(SourceChangedError):
        devices.pull("Мой мир")
    assert read_world(devices.mac_saves / "Мой мир") == before


def test_staging_is_next_to_worlds_and_removed(devices: Devices) -> None:
    tmp = staging_root(devices.mac_saves)
    pull_world(devices.client, "Мой мир", devices.mac_saves, tmp, devices.mac_backups, 3)
    assert not tmp.exists()
    assert [p.name for p in devices.mac_saves.iterdir()] == ["Мой мир"]


def test_sync_state_is_shared_with_other_computer(devices: Devices) -> None:
    """Подсказку можно дать и на компьютере, который мир отдавал: он спрашивает состояние у получателя."""
    assert devices.client.get_sync_state("Мой мир") is None  # сервер его не знает
    saved = SyncState(datetime(2026, 10, 20, 18, 0), {"a": FileEntry("a", 1, 5)})
    devices.server.sync_state = lambda instance, world: saved if (instance, world) == (None, "Мой мир") else None

    assert devices.client.get_sync_state("Мой мир") == saved
    assert devices.client.get_sync_state("Другой мир") is None
