"""Проверки каркаса: пакет импортируется, ресурсы находятся, окно создаётся."""

import sys
from pathlib import Path

import pytest

import blocksync
from blocksync.resources import resource_path, resources_dir


def test_package_has_version() -> None:
    assert blocksync.__version__


def test_resources_dir_exists_in_sources() -> None:
    assert resources_dir().is_dir()


def test_resource_path_inside_pyinstaller_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Имитируем собранное приложение: PyInstaller задаёт sys._MEIPASS.
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    expected = tmp_path / "blocksync" / "resources" / "fonts" / "font.ttf"
    assert resource_path("fonts/font.ttf") == expected
