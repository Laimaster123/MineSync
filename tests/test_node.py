"""Два узла на одном компьютере: находят друг друга, сопрягаются, общаются, забывают пару."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from blocksync.config.app_lock import AlreadyRunningError
from blocksync.config.paths import AppPaths
from blocksync.config.settings import load_settings, save_settings
from blocksync.net.discovery import PeerStatus
from blocksync.net.node import Node

from helpers import make_level_dat, make_world
from test_discovery import wait_until


def make_node(root: Path, name: str, saves: Path) -> Node:
    paths = AppPaths(root)
    settings = load_settings(paths.settings_file)
    settings.device_name = name
    settings.saves_dir = saves
    save_settings(settings, paths.settings_file)
    return Node(paths)


@pytest.fixture
def nodes(tmp_path: Path) -> Iterator[tuple[Node, Node]]:
    pc_saves = tmp_path / "pc" / "saves"
    make_level_dat(make_world(pc_saves / "World", {"a.dat": b"a"}))
    pc = make_node(tmp_path / "pc" / "app", "ПК", pc_saves)
    mac = make_node(tmp_path / "mac" / "app", "Мак", tmp_path / "mac" / "saves")
    pc.start()
    mac.start()
    try:
        yield pc, mac
    finally:
        pc.stop()
        mac.stop()


def test_full_pairing_scenario(nodes: tuple[Node, Node], tmp_path: Path) -> None:
    pc, mac = nodes
    assert wait_until(lambda: mac.discovery.find(pc.settings.device_id) is not None)
    assert mac.status() is PeerStatus.NOT_PAIRED

    # На ПК показали код, на Маке его ввели.
    code = pc.show_code()
    mac.pair(mac.discovery.find(pc.settings.device_id), code)  # type: ignore[arg-type]

    assert mac.paired is not None and mac.paired.name == "ПК"
    assert pc.paired is not None and pc.paired.name == "Мак"
    assert wait_until(lambda: pc.status() is PeerStatus.ONLINE)
    assert mac.status() is PeerStatus.ONLINE

    # Пара сохранилась в настройках на диске.
    saved = load_settings(AppPaths(tmp_path / "mac" / "app").settings_file)
    assert saved.paired == mac.paired

    # Сопряжённые устройства общаются по HTTPS с подписью.
    # Миры запрашиваются у конкретной сборки ПК (папка saves из настроек стала сборкой).
    [pc_instance] = pc.instances()
    client = mac.peer_client(pc_instance.id)
    assert client is not None
    with client:
        assert [w.folder_name for w in client.list_worlds()] == ["World"]

    # «Забыть устройство» удаляет пару на обоих.
    mac.forget()
    assert mac.paired is None
    assert pc.paired is None
    assert mac.status() is PeerStatus.NOT_PAIRED


def test_second_copy_reports_already_running(tmp_path: Path) -> None:
    """Вторая копия программы с теми же настройками не падает, а сообщает, что уже запущена."""
    first = make_node(tmp_path / "app", "ПК", tmp_path / "saves")
    first.start()
    try:
        second = Node(AppPaths(tmp_path / "app"))
        with pytest.raises(AlreadyRunningError):
            second.start()
    finally:
        first.stop()


def test_lock_is_released_after_stop(tmp_path: Path) -> None:
    """После закрытия программы её можно снова запустить."""
    first = make_node(tmp_path / "app", "ПК", tmp_path / "saves")
    first.start()
    first.stop()
    again = Node(AppPaths(tmp_path / "app"))
    again.start()
    again.stop()


def test_peer_rename_is_picked_up(nodes: tuple[Node, Node]) -> None:
    """Сменили имя в настройках ПК — Мак сам показывает новое имя пары."""
    pc, mac = nodes
    assert wait_until(lambda: mac.discovery.find(pc.settings.device_id) is not None)
    mac.pair(mac.discovery.find(pc.settings.device_id), pc.show_code())  # type: ignore[arg-type]

    s = pc.settings
    pc.update_settings("DESKTOP-NEW-NAME", s.saves_dir, s.backups_dir, s.keep_backups)

    assert wait_until(lambda: mac.paired is not None and mac.paired.name == "DESKTOP-NEW-NAME")
