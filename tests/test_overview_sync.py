"""Список миров со статусами и подготовка синхронизации между двумя «устройствами»."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from blocksync.core.checks import NotEnoughSpaceError, SyncBlocker, SyncWarning
from blocksync.net import pull as pull_module
from blocksync.net.auth import RequestVerifier
from blocksync.net.client import PeerClient
from blocksync.net.overview import WorldStatus, build_overview, load_icons
from blocksync.net.pull import pull_world
from blocksync.net.server import SyncServer
from blocksync.net.sync import Direction, prepare_sync

from helpers import BASE_TIME, hold_lock, make_level_dat, make_world, write_file

KEY = b"k" * 32


@dataclass
class Pair:
    pc_saves: Path  # «другое устройство» (сервер)
    mac_saves: Path  # «это устройство»
    mac_tmp: Path
    client: PeerClient


def world(saves: Path, name: str, files: dict[str, bytes], mtime: float = BASE_TIME) -> Path:
    path = make_world(saves / name, files, mtime=mtime)
    make_level_dat(path, level_name=name)
    return path


@pytest.fixture
def pair(tmp_path: Path) -> Iterator[Pair]:
    pc_saves, mac_saves = tmp_path / "pc", tmp_path / "mac"
    pc_saves.mkdir()
    mac_saves.mkdir()
    server = SyncServer(pc_saves, RequestVerifier(lambda device: KEY))
    server.start()
    client = PeerClient(f"http://127.0.0.1:{server.port}", "mac", KEY)
    try:
        yield Pair(pc_saves, mac_saves, tmp_path / "tmp", client)
    finally:
        client.close()
        server.stop()


def test_statuses(pair: Pair) -> None:
    world(pair.pc_saves, "Одинаковый", {"a": b"1"})
    world(pair.mac_saves, "Одинаковый", {"a": b"1"})
    world(pair.pc_saves, "Разный", {"a": b"1"})
    world(pair.mac_saves, "Разный", {"a": b"22"})
    world(pair.pc_saves, "Там", {"a": b"1"})
    world(pair.mac_saves, "Здесь", {"a": b"1"})
    world(pair.mac_saves, "Открыт", {"session.lock": b"x"})

    with hold_lock(pair.mac_saves / "Открыт" / "session.lock"):
        overview = build_overview(pair.mac_saves, pair.client)

    statuses = {row.folder_name: row.status for row in overview.rows}
    assert statuses == {
        "Одинаковый": WorldStatus.SAME,
        "Разный": WorldStatus.DIFFERENT,
        "Там": WorldStatus.ONLY_THERE,
        "Здесь": WorldStatus.ONLY_HERE,
        "Открыт": WorldStatus.LOCKED,
    }
    assert not overview.saves_missing and overview.peer_error is None


def test_peer_offline_and_missing_saves(tmp_path: Path) -> None:
    world(tmp_path / "saves", "Мир", {"a": b"1"})
    overview = build_overview(tmp_path / "saves", None)
    assert [row.status for row in overview.rows] == [WorldStatus.UNKNOWN]

    missing = build_overview(tmp_path / "нет такой папки", None)
    assert missing.saves_missing and missing.rows == []


def test_prepare_pull_shows_plan_and_warnings(pair: Pair) -> None:
    world(pair.pc_saves, "Мир", {"a": b"new"}, mtime=BASE_TIME)
    world(pair.mac_saves, "Мир", {"a": b"old", "b": b"x", "c": b"y"}, mtime=BASE_TIME + 1000)

    preview = prepare_sync(pair.client, "Мир", Direction.PULL, pair.mac_saves, pair.mac_tmp)

    assert [e.path for e in preview.plan.to_copy] == ["a"]
    assert sorted(e.path for e in preview.plan.to_delete) == ["b", "c"]
    assert SyncWarning.RECEIVER_NEWER in preview.check.warnings
    assert SyncWarning.MASS_DELETE in preview.check.warnings
    assert preview.check.can_sync


def test_push_of_missing_world_is_blocked(pair: Pair) -> None:
    # Мир есть только на ПК, а пользователь сказал, что играл здесь:
    # иначе ПК потерял бы мир целиком.
    world(pair.pc_saves, "Мир", {"a": b"1"})

    preview = prepare_sync(pair.client, "Мир", Direction.PUSH, pair.mac_saves, pair.mac_tmp)

    assert SyncBlocker.SOURCE_MISSING in preview.check.blockers


def test_pull_checks_free_space(pair: Pair, monkeypatch: pytest.MonkeyPatch) -> None:
    world(pair.pc_saves, "Мир", {"a": b"1"})
    monkeypatch.setattr("blocksync.core.checks.free_space", lambda path: 0)

    with pytest.raises(NotEnoughSpaceError):
        pull_world(pair.client, "Мир", pair.mac_saves, pair.mac_tmp, pair.mac_tmp / "b", keep_backups=3)
    assert not (pair.mac_saves / "Мир").exists()


def test_pull_rejects_unsafe_world_name(pair: Pair) -> None:
    with pytest.raises(pull_module.NetworkError):
        pull_world(pair.client, "..", pair.mac_saves, pair.mac_tmp, pair.mac_tmp / "b", keep_backups=3)


def test_mismatch_after_change_is_detected(pair: Pair) -> None:
    world(pair.pc_saves, "Мир", {"a": b"1"})
    pull_world(pair.client, "Мир", pair.mac_saves, pair.mac_tmp, pair.mac_tmp / "b", keep_backups=3)
    assert build_overview(pair.mac_saves, pair.client).rows[0].status is WorldStatus.SAME

    write_file(pair.pc_saves / "Мир" / "a", b"changed", mtime=BASE_TIME + 100)
    assert build_overview(pair.mac_saves, pair.client).rows[0].status is WorldStatus.DIFFERENT


def test_icons_loaded_locally_and_over_network(pair: Pair) -> None:
    world(pair.mac_saves, "Здесь", {"icon.png": b"local png"})
    world(pair.pc_saves, "Там", {"icon.png": b"remote png"})
    world(pair.pc_saves, "Без иконки", {"a": b"1"})

    rows = build_overview(pair.mac_saves, pair.client).rows
    assert load_icons(rows, pair.client) == {"Здесь": b"local png", "Там": b"remote png"}
