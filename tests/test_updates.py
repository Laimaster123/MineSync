"""Проверка обновлений на GitHub (без настоящей сети: ответы GitHub подставляются)."""

from pathlib import Path
from typing import Any

import httpx
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from blocksync.config.settings import load_settings
from blocksync.net.updates import Release, check_for_update, is_newer, parse_version, release_from_json
from blocksync.ui.update_dialog import UpdateDialog

from test_node import make_node

RELEASE = {
    "tag_name": "v1.2.0",
    "html_url": "https://github.com/Laimaster123/MineSync/releases/tag/v1.2.0",
    "body": "- Новая функция",
    "draft": False,
    "prerelease": False,
    "assets": [
        {"name": "MineSync-1.2.0-Windows.zip", "browser_download_url": "https://example.com/win.zip"},
        {"name": "MineSync-1.2.0-macOS.zip", "browser_download_url": "https://example.com/mac.zip"},
    ],
}


def github(status: int = 200, data: Any = RELEASE) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=data))


def test_versions_are_compared_as_numbers() -> None:
    assert parse_version("v1.10.0") == (1, 10, 0)
    assert parse_version("1.2.0-beta") is None
    assert is_newer("1.10.0", "1.9.3")
    assert not is_newer("1.2", "1.2.0")
    assert not is_newer("1.0.0", "1.1.0")
    assert not is_newer("мусор", "1.0.0")


def test_download_link_for_this_system() -> None:
    assert release_from_json(RELEASE, "win32").download_url == "https://example.com/win.zip"
    assert release_from_json(RELEASE, "darwin").download_url == "https://example.com/mac.zip"
    # Для Linux архива нет — открываем страницу выпуска.
    assert release_from_json(RELEASE, "linux").download_url == RELEASE["html_url"]


def test_newer_release_is_found() -> None:
    release = check_for_update("1.1.0", transport=github())
    assert release is not None and release.version == "1.2.0" and "Новая функция" in release.notes


def test_same_or_older_version_is_not_offered() -> None:
    assert check_for_update("1.2.0", transport=github()) is None
    assert check_for_update("2.0.0", transport=github()) is None


def test_errors_are_silent() -> None:
    assert check_for_update("1.0.0", transport=github(status=403)) is None
    assert check_for_update("1.0.0", transport=github(data={"неверный": "ответ"})) is None
    assert check_for_update("1.0.0", transport=github(data={**RELEASE, "prerelease": True})) is None

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети")

    assert check_for_update("1.0.0", transport=httpx.MockTransport(offline)) is None


def test_update_dialog_skip_remembers_version(qtbot: QtBot, tmp_path: Path) -> None:
    node = make_node(tmp_path, "ПК", tmp_path / "saves")
    dialog = UpdateDialog(node, Release("1.2.0", "https://x", "https://x/a.zip", "**Что нового**"))
    qtbot.addWidget(dialog)
    assert any("1.2.0" in label.text() for label in dialog.findChildren(QLabel))

    dialog.skip_button.click()

    assert node.settings.skipped_version == "1.2.0"
    assert load_settings(node.paths.settings_file).skipped_version == "1.2.0"
