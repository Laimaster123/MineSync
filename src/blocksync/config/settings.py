"""Настройки приложения и данные о сопряжённом устройстве.

Хранятся в файле settings.json в папке данных приложения. В файле лежит
общий секретный ключ пары, поэтому читать его может только владелец
(на macOS права 600; на Windows папка AppData и так доступна только пользователю).
"""

import json
import logging
import os
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blocksync.config.instances import Instance, instance_from_json, instance_to_json
from blocksync.core.backups import DEFAULT_BACKUPS, validate_keep

logger = logging.getLogger(__name__)


@dataclass
class PairedDevice:
    """Устройство, с которым установлена пара."""

    device_id: str
    name: str
    key: bytes  # общий секретный ключ для подписи запросов
    cert_pem: str  # сертификат устройства: по нему проверяем, что говорим именно с ним


@dataclass
class Settings:
    device_id: str  # уникальный ID этого устройства, создаётся при первом запуске
    device_name: str  # имя, которое видит второе устройство
    saves_dir: Path | None = None  # None — стандартная папка (config.paths.default_saves_dir)
    backups_dir: Path | None = None  # None — папка backups внутри папки данных
    keep_backups: int = DEFAULT_BACKUPS
    full_backup: bool = False  # True — перед синхронизацией сохранять весь мир (медленнее, нагружает диск)
    paired: PairedDevice | None = field(default=None)
    world_notes: dict[str, str] = field(default_factory=dict)  # описания миров: {папка мира: текст}
    instances: list[Instance] = field(default_factory=list)  # сборки, добавленные пользователем
    instance_links: dict[str, str] = field(default_factory=dict)  # {наша сборка: сборка на втором устройстве}
    selected_tab: str | None = None  # открытая вкладка: ID сборки или "recent"
    language: str = "ru"
    check_updates: bool = True  # при запуске проверять, нет ли новой версии на GitHub
    skipped_version: str | None = None  # о какой версии пользователь попросил больше не напоминать


def default_device_name() -> str:
    """Имя компьютера из системы. Пользователь может поменять его в настройках.

    На macOS берём имя из «Системных настроек» («MacBook Air — Алекс»),
    иначе — сетевое имя компьютера («DESKTOP-7F3K2L») без хвоста ".local".
    """
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    name = socket.gethostname().removesuffix(".local")
    return name or "Компьютер"


def new_settings() -> Settings:
    return Settings(device_id=uuid.uuid4().hex, device_name=default_device_name())


def load_settings(path: Path) -> Settings:
    """Прочитать настройки. Если файла нет — создать новые (с новым ID) и сохранить."""
    return read_settings(path)[0]


def read_settings(path: Path) -> tuple[Settings, Path | None]:
    """Прочитать настройки; второе значение — куда отложен повреждённый файл (или None).

    Повреждённый файл (например, его испортили вручную) не должен мешать запуску:
    он переименовывается в settings.broken.json, а программа начинает с новых настроек.
    """
    if not path.exists():
        settings = new_settings()
        save_settings(settings, path)
        return settings, None
    try:
        return _from_json(json.loads(path.read_text(encoding="utf-8"))), None
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        broken = path.with_name(f"{path.stem}.broken{path.suffix}")
        logger.error("Файл настроек %s повреждён (%s), он сохранён как %s", path, error, broken)
        os.replace(path, broken)
        settings = new_settings()
        save_settings(settings, path)
        return settings, broken


def save_settings(settings: Settings, path: Path) -> None:
    """Сохранить настройки. Сначала пишем во временный файл, потом подменяем:
    так при сбое посреди записи старый файл останется целым."""
    validate_keep(settings.keep_backups)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    # Создаём файл сразу с правами «только владелец» (на Windows права игнорируются).
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(_to_json(settings), file, ensure_ascii=False, indent=2)
    os.replace(temp, path)
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def _to_json(settings: Settings) -> dict[str, Any]:
    paired = settings.paired
    return {
        "device_id": settings.device_id,
        "device_name": settings.device_name,
        "saves_dir": str(settings.saves_dir) if settings.saves_dir else None,
        "backups_dir": str(settings.backups_dir) if settings.backups_dir else None,
        "keep_backups": settings.keep_backups,
        "full_backup": settings.full_backup,
        "world_notes": settings.world_notes,
        "instances": [instance_to_json(i) for i in settings.instances],
        "instance_links": settings.instance_links,
        "selected_tab": settings.selected_tab,
        "language": settings.language,
        "check_updates": settings.check_updates,
        "skipped_version": settings.skipped_version,
        "paired": None if paired is None else {
            "device_id": paired.device_id,
            "name": paired.name,
            "key": paired.key.hex(),
            "cert_pem": paired.cert_pem,
        },
    }


def _from_json(data: dict[str, Any]) -> Settings:
    paired_data = data.get("paired")
    paired = None
    if paired_data:
        paired = PairedDevice(
            device_id=paired_data["device_id"],
            name=paired_data["name"],
            key=bytes.fromhex(paired_data["key"]),
            cert_pem=paired_data["cert_pem"],
        )
    return Settings(
        device_id=data["device_id"],
        device_name=data["device_name"],
        saves_dir=Path(data["saves_dir"]) if data.get("saves_dir") else None,
        backups_dir=Path(data["backups_dir"]) if data.get("backups_dir") else None,
        keep_backups=data.get("keep_backups", DEFAULT_BACKUPS),
        full_backup=bool(data.get("full_backup", False)),
        paired=paired,
        world_notes=dict(data.get("world_notes") or {}),
        instances=[instance_from_json(item) for item in data.get("instances") or []],
        instance_links=dict(data.get("instance_links") or {}),
        selected_tab=data.get("selected_tab"),
        language=data.get("language") or "ru",
        check_updates=bool(data.get("check_updates", True)),
        skipped_version=data.get("skipped_version"),
    )
