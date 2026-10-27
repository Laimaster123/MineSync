"""Шифрование соединения (HTTPS/TLS) между сопряжёнными устройствами.

У каждого устройства есть своя пара «закрытый ключ + сертификат». Сертификат —
это открытая часть: по нему собеседник проверяет, что говорит именно с этим
устройством. При сопряжении устройства обмениваются сертификатами и дальше
доверяют только им («закрепление сертификата»). Поэтому подслушать или
подменить передаваемые файлы в сети уже нельзя.
"""

import datetime
import hashlib
import os
import ssl
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"
CERT_VALID_YEARS = 100


def ensure_certificate(tls_dir: Path, device_id: str) -> tuple[Path, Path]:
    """Вернуть пути к сертификату и ключу устройства, создав их при первом запуске."""
    cert_path, key_path = tls_dir / CERT_FILE, tls_dir / KEY_FILE
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    tls_dir.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"MineSync {device_id}")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)  # сертификат подписан сам собой
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365 * CERT_VALID_YEARS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _write_private(key_path, key_bytes)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    """Настройки TLS для сервера этого устройства."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context


def client_context(peer_cert_pem: str) -> ssl.SSLContext:
    """Настройки TLS для клиента: доверяем только сертификату сопряжённого устройства.

    Имя хоста не проверяем: IP-адрес устройства в домашней сети может меняться,
    а подлинность и так гарантирует закреплённый сертификат.
    """
    context = ssl.create_default_context(cadata=peer_cert_pem)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def insecure_client_context() -> ssl.SSLContext:
    """TLS без проверки сертификата — только для сопряжения.

    При сопряжении сертификата собеседника ещё нет. Подлинность там
    проверяется по 6-значному коду (см. pairing.py).
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def fingerprint(cert_pem: str) -> str:
    """Короткий «отпечаток» сертификата для логов: первые символы SHA-256."""
    der = x509.load_pem_x509_certificate(cert_pem.encode()).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()[:16]


def _write_private(path: Path, data: bytes) -> None:
    """Записать файл с правами «только владелец»."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)
    if sys.platform != "win32":
        os.chmod(path, 0o600)
