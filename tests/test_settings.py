import stat
import sys
from pathlib import Path

import pytest

from blocksync.config.paths import AppPaths, default_saves_dir, migrate_data_dir
from blocksync.config.settings import PairedDevice, load_settings, read_settings, save_settings


def test_first_load_creates_settings_with_new_id(tmp_path: Path) -> None:
    path = AppPaths(tmp_path).settings_file

    first = load_settings(path)
    second = load_settings(path)

    assert path.exists()
    assert len(first.device_id) == 32
    assert first.device_id == second.device_id  # ID не меняется между запусками
    assert first.device_name


def test_other_device_gets_other_id(tmp_path: Path) -> None:
    a = load_settings(tmp_path / "a" / "settings.json")
    b = load_settings(tmp_path / "b" / "settings.json")
    assert a.device_id != b.device_id


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = load_settings(path)
    settings.saves_dir = tmp_path / "Мои миры"
    settings.keep_backups = 7
    settings.paired = PairedDevice("abc", "Домашний ПК", b"\x01" * 32, "-----BEGIN CERTIFICATE-----")

    save_settings(settings, path)

    assert load_settings(path) == settings


@pytest.mark.skipif(sys.platform == "win32", reason="права доступа Unix")
def test_settings_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    load_settings(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_invalid_keep_backups_not_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = load_settings(path)
    settings.keep_backups = 11
    with pytest.raises(ValueError):
        save_settings(settings, path)


def test_default_saves_dir_ends_with_saves() -> None:
    assert default_saves_dir().name == "saves"


def test_old_data_dir_is_migrated(tmp_path: Path) -> None:
    old, new = tmp_path / "BlockSync", tmp_path / "MineSync"
    (old / "backups" / "Мир").mkdir(parents=True)
    (old / "settings.json").write_text("{}")

    migrate_data_dir(old, new)

    assert not old.exists()
    assert (new / "settings.json").read_text() == "{}"
    assert (new / "backups" / "Мир").is_dir()
    migrate_data_dir(old, new)  # повторный вызов ничего не ломает


def test_broken_settings_are_set_aside(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ это не JSON", encoding="utf-8")

    settings, broken = read_settings(path)

    assert broken == tmp_path / "settings.broken.json"
    assert broken.read_text(encoding="utf-8") == "{ это не JSON"
    assert len(settings.device_id) == 32
    assert load_settings(path) == settings  # новые настройки сохранены
