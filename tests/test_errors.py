"""Тексты ошибок для пользователя: на выбранном языке интерфейса."""

from collections.abc import Iterator

import pytest

from blocksync.core.apply import SyncError
from blocksync.net.client import NetworkError, SourceChangedError
from blocksync.net.pairing import PairingError
from blocksync.ui.errors import describe_error
from blocksync.ui.strings import set_language


@pytest.fixture
def english() -> Iterator[None]:
    set_language("en")
    yield
    set_language("ru")


def test_network_errors_are_translated(english: None) -> None:
    assert describe_error(NetworkError("не удалось подключиться", "net_connect")).startswith("Could not connect")
    assert "503" in describe_error(NetworkError("ошибка 503", "net_status", status=503))
    assert describe_error(SourceChangedError("файл изменился")).startswith("The world on the other computer changed")


def test_pairing_and_sync_errors_are_translated(english: None) -> None:
    assert describe_error(PairingError("неверный код", code="pair_wrong_code", left=2)) == "Wrong code. Attempts left: 2."
    assert "a.dat" in describe_error(SyncError("размер не совпал", "sync_size_mismatch", path="a.dat"))


def test_unknown_error_kind_keeps_original_text() -> None:
    """Ошибку без вида (например, от старой версии программы) показываем как есть."""
    assert describe_error(PairingError("что-то пошло не так")) == "Что-то пошло не так."
    assert describe_error(NetworkError("странное", "no_such_code")) == "Странное."
