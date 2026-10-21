"""Обнаружение: два экземпляра на одном компьютере находят друг друга через mDNS."""

import ipaddress
import time
import uuid
from collections.abc import Callable

from blocksync.config.settings import PairedDevice
from blocksync.net.discovery import (
    Discovery,
    FoundDevice,
    PeerStatus,
    choose_address,
    is_lan_address,
    peer_status,
)


def wait_until(condition: Callable[[], bool], timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


def test_two_devices_find_each_other() -> None:
    pc_id, mac_id = uuid.uuid4().hex, uuid.uuid4().hex
    pc = Discovery(pc_id, "ПК", 8001)
    mac = Discovery(mac_id, "Мак", 8002)
    pc.start()
    mac.start()
    try:
        assert wait_until(lambda: mac.find(pc_id) is not None), "Мак не нашёл ПК"
        assert wait_until(lambda: pc.find(mac_id) is not None), "ПК не нашёл Мак"
        found_pc = mac.find(pc_id)
        assert found_pc is not None and found_pc.name == "ПК" and found_pc.port == 8001
        assert all(d.device_id != mac_id for d in mac.devices())  # себя не видим

        pc.stop()
        assert wait_until(lambda: mac.find(pc_id) is None), "ПК не пропал после остановки"
    finally:
        pc.stop()
        mac.stop()


def test_peer_status() -> None:
    paired = PairedDevice("pc", "ПК", b"k" * 32, "cert")
    online = [FoundDevice("pc", "ПК", ("192.168.1.2",), 8001)]
    other = [FoundDevice("someone", "Чужой", ("192.168.1.3",), 8001)]

    assert peer_status(None, online) is PeerStatus.NOT_PAIRED
    assert peer_status(paired, online) is PeerStatus.ONLINE
    assert peer_status(paired, other) is PeerStatus.OFFLINE


def test_choose_address_prefers_same_network() -> None:
    local = [ipaddress.IPv4Interface("192.168.1.72/24"), ipaddress.IPv4Interface("198.18.0.1/16")]
    # Адрес VPN (198.18.x.x) идёт первым, но выбрать нужно домашний.
    assert choose_address(("198.18.0.1", "192.168.1.10"), local) == "192.168.1.10"


def test_choose_address_falls_back_to_lan() -> None:
    local = [ipaddress.IPv4Interface("10.0.0.5/24")]
    assert choose_address(("198.18.0.1", "192.168.1.10"), local) == "192.168.1.10"


def test_vpn_addresses_are_not_lan() -> None:
    assert is_lan_address("192.168.1.72")
    assert is_lan_address("10.1.2.3")
    assert is_lan_address("172.20.0.1")
    assert not is_lan_address("198.18.0.1")
    assert not is_lan_address("8.8.8.8")
