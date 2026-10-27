"""Общие настройки тестов."""

import os

# Окна в тестах не должны появляться на экране.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from blocksync.net import client as client_module


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """В тестах при обрыве связи повторяем сразу, без пауз (иначе тесты ждали бы ~30 секунд)."""
    monkeypatch.setattr(client_module, "RETRY_DELAYS_SECONDS", (0, 0))
