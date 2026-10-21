"""Обмен по HTTPS с закреплённым сертификатом."""

import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from blocksync.net.auth import RequestVerifier
from blocksync.net.client import NetworkError, PeerClient
from blocksync.net.server import SyncServer
from blocksync.net.tls import client_context, ensure_certificate, fingerprint, server_context
from helpers import make_level_dat, make_world

KEY = b"k" * 32


@pytest.fixture
def https_server(tmp_path: Path) -> Iterator[tuple[SyncServer, str]]:
    saves = tmp_path / "saves"
    make_level_dat(make_world(saves / "World", {"a.dat": b"a"}))
    cert, key = ensure_certificate(tmp_path / "pc_tls", "pc")
    server = SyncServer(saves, RequestVerifier(lambda d: KEY), ssl_context=server_context(cert, key))
    server.start()
    try:
        yield server, cert.read_text()
    finally:
        server.stop()


def test_certificate_created_once(tmp_path: Path) -> None:
    cert1, key1 = ensure_certificate(tmp_path, "dev")
    pem = cert1.read_text()
    cert2, _ = ensure_certificate(tmp_path, "dev")
    assert cert2.read_text() == pem
    assert len(fingerprint(pem)) == 16
    if sys.platform != "win32":
        assert stat.S_IMODE(key1.stat().st_mode) == 0o600


def test_https_with_pinned_certificate(https_server: tuple[SyncServer, str]) -> None:
    server, cert_pem = https_server
    with PeerClient(f"https://127.0.0.1:{server.port}", "mac", KEY, client_context(cert_pem)) as client:
        assert [w.folder_name for w in client.list_worlds()] == ["World"]
        with client.open_file("World", "a.dat") as file:
            assert file.read() == b"a"


def test_other_certificate_is_rejected(https_server: tuple[SyncServer, str], tmp_path: Path) -> None:
    server, _ = https_server
    # Злоумышленник подсовывает свой сертификат вместо сертификата ПК.
    other_cert, _ = ensure_certificate(tmp_path / "attacker", "attacker")
    context = client_context(other_cert.read_text())
    with PeerClient(f"https://127.0.0.1:{server.port}", "mac", KEY, context) as client:
        with pytest.raises(NetworkError, match="сертификат"):
            client.list_worlds()


def test_plain_http_to_https_server_fails(https_server: tuple[SyncServer, str]) -> None:
    server, _ = https_server
    with PeerClient(f"http://127.0.0.1:{server.port}", "mac", KEY) as client:
        with pytest.raises(NetworkError):
            client.list_worlds()
