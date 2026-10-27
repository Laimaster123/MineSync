"""Сопряжение по коду через настоящий HTTPS-сервер на этом компьютере."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from blocksync.config.settings import PairedDevice
from blocksync.net.auth import RequestVerifier
from blocksync.net.client import PeerClient
from spake2 import SPAKE2_A

from blocksync.net.pairing import (
    CODE_LIFETIME_SECONDS, MAX_ATTEMPTS, MAX_PENDING, PairingError, PairingHost, pair_with,
)
from blocksync.net.server import SyncServer
from blocksync.net.tls import client_context, ensure_certificate, server_context


@dataclass
class Host:
    """Устройство, которое показывает код («ПК»)."""

    pairing: PairingHost
    server: SyncServer
    url: str
    cert_pem: str
    paired: list[PairedDevice] = field(default_factory=list)
    now: list[float] = field(default_factory=lambda: [0.0])


@pytest.fixture
def host(tmp_path: Path) -> Iterator[Host]:
    cert, key = ensure_certificate(tmp_path / "pc_tls", "pc-id")
    cert_pem = cert.read_text()
    paired: list[PairedDevice] = []
    now = [0.0]
    pairing = PairingHost("pc-id", "ПК", cert_pem, paired.append, clock=lambda: now[0])

    def key_for(device: str) -> bytes | None:
        return next((p.key for p in paired if p.device_id == device), None)

    server = SyncServer(tmp_path / "saves", RequestVerifier(key_for), pairing=pairing,
                        ssl_context=server_context(cert, key))
    server.start()
    try:
        yield Host(pairing, server, f"https://127.0.0.1:{server.port}", cert_pem, paired, now)
    finally:
        server.stop()


@pytest.fixture
def guest_cert(tmp_path: Path) -> str:
    cert, _ = ensure_certificate(tmp_path / "mac_tls", "mac-id")
    return cert.read_text()


def test_successful_pairing(host: Host, guest_cert: str) -> None:
    code = host.pairing.start_code()

    result = pair_with(host.url, code, "mac-id", "Мак", guest_cert)

    [host_side] = host.paired
    assert result.device_id == "pc-id" and result.name == "ПК"
    assert host_side.device_id == "mac-id" and host_side.name == "Мак"
    assert result.key == host_side.key and len(result.key) == 32
    assert result.cert_pem == host.cert_pem
    assert host_side.cert_pem == guest_cert
    assert not host.pairing.is_active  # код одноразовый


def test_paired_devices_can_talk_securely(host: Host, guest_cert: str) -> None:
    result = pair_with(host.url, host.pairing.start_code(), "mac-id", "Мак", guest_cert)
    host.server.saves_dir.mkdir(parents=True)

    with PeerClient(host.url, "mac-id", result.key, client_context(result.cert_pem)) as client:
        assert client.list_worlds() == []


def test_wrong_code_is_rejected_and_counts_attempts(host: Host, guest_cert: str) -> None:
    code = host.pairing.start_code()
    wrong = "000000" if code != "000000" else "111111"

    with pytest.raises(PairingError, match=f"осталось попыток: {MAX_ATTEMPTS - 1}"):
        pair_with(host.url, wrong, "mac-id", "Мак", guest_cert)

    # Правильный код всё ещё работает.
    pair_with(host.url, code, "mac-id", "Мак", guest_cert)
    assert len(host.paired) == 1


def test_code_invalid_after_max_attempts(host: Host, guest_cert: str) -> None:
    code = host.pairing.start_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(PairingError):
            pair_with(host.url, wrong, "mac-id", "Мак", guest_cert)

    with pytest.raises(PairingError, match="нет активного кода"):
        pair_with(host.url, code, "mac-id", "Мак", guest_cert)
    assert host.paired == []


def test_expired_code_is_rejected(host: Host, guest_cert: str) -> None:
    code = host.pairing.start_code()
    host.now[0] += CODE_LIFETIME_SECONDS + 1

    with pytest.raises(PairingError, match="нет активного кода"):
        pair_with(host.url, code, "mac-id", "Мак", guest_cert)


def test_no_code_shown(host: Host, guest_cert: str) -> None:
    with pytest.raises(PairingError, match="нет активного кода"):
        pair_with(host.url, "123456", "mac-id", "Мак", guest_cert)


def test_code_cannot_be_reused(host: Host, guest_cert: str) -> None:
    code = host.pairing.start_code()
    pair_with(host.url, code, "mac-id", "Мак", guest_cert)

    with pytest.raises(PairingError):
        pair_with(host.url, code, "evil-id", "Чужой", guest_cert)
    assert [p.device_id for p in host.paired] == ["mac-id"]


def test_new_code_replaces_old(host: Host, guest_cert: str) -> None:
    old = host.pairing.start_code()
    new = host.pairing.start_code()
    if old == new:
        pytest.skip("случайно совпали коды")

    with pytest.raises(PairingError):
        pair_with(host.url, old, "mac-id", "Мак", guest_cert)
    pair_with(host.url, new, "mac-id", "Мак", guest_cert)


def test_codes_are_six_digits(host: Host) -> None:
    codes = {host.pairing.start_code() for _ in range(50)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(codes) > 1


def test_unpair_notifies_host(host: Host, guest_cert: str) -> None:
    result = pair_with(host.url, host.pairing.start_code(), "mac-id", "Мак", guest_cert)
    forgotten: list[str] = []
    host.server.on_unpair = forgotten.append

    with PeerClient(host.url, "mac-id", result.key, client_context(result.cert_pem)) as client:
        client.unpair()

    assert forgotten == ["mac-id"]


def test_wrong_code_error_has_code_for_translation(host: Host, guest_cert: str) -> None:
    """Хозяин передаёт гостю вид ошибки, чтобы текст показался на языке гостя."""
    code = host.pairing.start_code()
    wrong = "000000" if code != "000000" else "111111"
    with pytest.raises(PairingError) as caught:
        pair_with(host.url, wrong, "mac-id", "Мак", guest_cert)
    assert caught.value.code == "pair_wrong_code"
    assert caught.value.params == {"left": MAX_ATTEMPTS - 1}


def test_strangers_cannot_block_pairing(host: Host, guest_cert: str) -> None:
    """Чужие незавершённые попытки не мешают настоящему гостю: старые попытки забываются."""
    code = host.pairing.start_code()
    for number in range(MAX_PENDING + 3):
        stranger = SPAKE2_A(b"123456", idA=b"blocksync-guest", idB=b"blocksync-host")
        host.pairing.handle_start({"device_id": f"stranger-{number}", "name": "?", "msg": stranger.start().hex()})

    pair_with(host.url, code, "mac-id", "Мак", guest_cert)

    assert [p.device_id for p in host.paired] == ["mac-id"]
