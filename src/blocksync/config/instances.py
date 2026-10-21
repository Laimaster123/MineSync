"""Сборки (instances): папки, в которых лежат миры, добавленные пользователем.

Пользователь выбирает папку сборки любого лаунчера (Prism, MultiMC, CurseForge,
Modrinth, обычный лаунчер…). Название сборки — последняя часть пути, а папку
с мирами программа ищет внутри сама: saves, minecraft/saves, .minecraft/saves,
а если их нет — папку saves глубже.
"""

import uuid
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from blocksync.core.world import LEVEL_DAT_NAME, is_world_dir

SAVES_NAME = "saves"
# Где обычно лежит папка миров внутри папки сборки (проверяются по порядку).
USUAL_PLACES = ["saves", "minecraft/saves", ".minecraft/saves", "game/saves"]
# Насколько глубоко искать папку saves, если её нет в обычных местах.
SEARCH_DEPTH = 4
# Папки, в которых миров не бывает, но файлов много: туда не заглядываем.
SKIP_DIRS = {"mods", "resourcepacks", "shaderpacks", "libraries", "assets", "versions", "natives",
             "logs", "crash-reports", "screenshots", "config", "cache", ".git", "node_modules"}


@dataclass(frozen=True)
class Instance:
    id: str  # постоянный ID (случайный), по нему сборки связываются между устройствами
    name: str  # название: последняя часть пути к папке сборки
    folder: Path  # папка сборки, которую выбрал пользователь
    saves_dir: Path  # папка с мирами внутри неё


class InstanceProblem(Enum):
    NOT_A_FOLDER = "not_a_folder"  # папки нет
    IS_WORLD = "is_world"  # выбрана папка одного мира, а не сборки
    NO_SAVES = "no_saves"  # внутри не нашлось папки с мирами
    ALREADY_ADDED = "already_added"  # эта сборка уже в списке


class InstanceError(Exception):
    """Сборку не удалось добавить; problem объясняет почему (текст подбирает интерфейс)."""

    def __init__(self, problem: InstanceProblem, folder: Path, existing: "Instance | None" = None) -> None:
        super().__init__(f"{problem.value}: {folder}")
        self.problem = problem
        self.folder = folder
        self.existing = existing


def _has_worlds(folder: Path) -> bool:
    try:
        return any(is_world_dir(child) for child in folder.iterdir())
    except OSError:
        return False


def find_saves_dir(folder: Path) -> Path | None:
    """Найти папку с мирами внутри папки сборки (или сама папка, если миры лежат прямо в ней)."""
    if folder.name == SAVES_NAME or _has_worlds(folder):
        return folder
    for place in USUAL_PLACES:
        candidate = folder / place
        if candidate.is_dir():
            return candidate
    # Поиск в ширину: сначала ближние папки, потом глубже.
    queue: deque[tuple[Path, int]] = deque([(folder, 0)])
    while queue:
        current, depth = queue.popleft()
        try:
            children = sorted(child for child in current.iterdir() if child.is_dir() and not child.is_symlink())
        except OSError:
            continue
        for child in children:
            if child.name == SAVES_NAME:
                return child
            if depth + 1 < SEARCH_DEPTH and child.name.lower() not in SKIP_DIRS:
                queue.append((child, depth + 1))
    return None


def make_instance(folder: Path, existing: list[Instance]) -> Instance:
    """Создать сборку из выбранной папки или выбросить InstanceError с причиной."""
    if not folder.is_dir():
        raise InstanceError(InstanceProblem.NOT_A_FOLDER, folder)
    if (folder / LEVEL_DAT_NAME).is_file():
        raise InstanceError(InstanceProblem.IS_WORLD, folder)
    saves = find_saves_dir(folder)
    if saves is None:
        raise InstanceError(InstanceProblem.NO_SAVES, folder)
    for instance in existing:
        if instance.saves_dir.resolve() == saves.resolve():
            raise InstanceError(InstanceProblem.ALREADY_ADDED, folder, instance)
    return Instance(uuid.uuid4().hex[:12], folder.name or str(folder), folder, saves)


def instance_from_saves(saves_dir: Path) -> Instance:
    """Сборка из старой настройки «папка saves» (до появления сборок).

    Название — папка сборки: …/instances/26.3/minecraft/saves → «26.3».
    """
    folder = saves_dir
    if folder.name == SAVES_NAME:
        folder = folder.parent
        if folder.name in ("minecraft", ".minecraft") and folder.parent != folder:
            folder = folder.parent
    return Instance(uuid.uuid4().hex[:12], folder.name or str(folder), folder, saves_dir)


def instance_to_json(instance: Instance) -> dict[str, Any]:
    return {"id": instance.id, "name": instance.name, "folder": str(instance.folder),
            "saves_dir": str(instance.saves_dir)}


def instance_from_json(data: dict[str, Any]) -> Instance:
    return Instance(data["id"], data["name"], Path(data["folder"]), Path(data["saves_dir"]))
