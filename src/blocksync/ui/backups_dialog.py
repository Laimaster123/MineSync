"""Окно «Бэкапы»: версии миров по датам, восстановление и удаление."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from blocksync.core.backups import Backup, delete_backup, list_backups, restore_backup
from blocksync.net.node import Node
from blocksync.ui.errors import describe_error
from blocksync.ui.strings import tr
from blocksync.ui.widgets import Dialog, Panel, set_variant
from blocksync.ui.worker import Task, run_task


@dataclass(frozen=True)
class BackupGroup:
    """Бэкапы одного мира одной сборки и куда их восстанавливать."""

    world: str  # имя папки мира
    title: str  # «Мир — сборка»
    backups: list[Backup]
    backups_root: Path  # где лежат бэкапы (туда же уйдёт текущая версия при восстановлении)
    target: Path | None  # папка мира, которую заменит восстановление (None — некуда)


def load_all_backups(node: Node) -> list[BackupGroup]:
    """Бэкапы всех сборок. Старые бэкапы (до появления сборок) лежат прямо в папке бэкапов —
    их восстанавливаем в ту сборку, где есть мир с таким же именем папки."""
    root = node.backups_dir
    instances = node.instances()
    groups: list[BackupGroup] = []
    for instance in instances:
        instance_root = node.backups_root_for(instance.id)
        for world in _world_folders(instance_root):
            groups.append(BackupGroup(
                world, tr("backups.group", world=world, instance=instance.name),
                list_backups(instance_root, world), instance_root, instance.saves_dir / world,
            ))
    instance_ids = {instance.id for instance in instances}
    for world in _world_folders(root):
        if world in instance_ids:
            continue
        home = next((i for i in instances if (i.saves_dir / world).is_dir()), instances[0] if instances else None)
        title = tr("backups.group", world=world, instance=home.name) if home else world
        groups.append(BackupGroup(world, title, list_backups(root, world), root,
                                  home.saves_dir / world if home else None))
    return [group for group in groups if group.backups]


def _world_folders(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(child.name for child in root.iterdir() if child.is_dir())


class BackupsDialog(Dialog):
    def __init__(self, node: Node, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.node = node
        self.task: Task | None = None
        self.setWindowTitle(tr("backups.title"))
        self.resize(680, 500)

        hint = QLabel(tr("backups.hint", path=node.backups_dir))
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("backups.column_world")])
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        self.restore_button = set_variant(QPushButton(tr("backups.restore")), "primary")
        self.delete_button = set_variant(QPushButton(tr("backups.delete")), "danger")
        close = QPushButton(tr("common.close"))
        self.restore_button.clicked.connect(self.restore_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch()
        buttons.addWidget(close)

        tree_panel = Panel()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(8, 8, 8, 8)
        tree_layout.addWidget(self.tree)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(hint)
        layout.addWidget(tree_panel, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

        self._update_buttons()
        self.reload()

    # --- Список ---

    def reload(self) -> None:
        node = self.node
        self._run(lambda task: load_all_backups(node), self._show)

    def _show(self, groups: list[BackupGroup]) -> None:
        self.tree.clear()
        for group in groups:
            world_item = QTreeWidgetItem([group.title])
            for backup in group.backups:
                kind = tr("backups.kind_delta") if backup.is_delta else tr("backups.kind_full")
                child = QTreeWidgetItem([f"{backup.created.strftime('%d.%m.%Y %H:%M:%S')}  ·  {kind}"])
                child.setData(0, Qt.ItemDataRole.UserRole, (backup, group))
                world_item.addChild(child)
            self.tree.addTopLevelItem(world_item)
            world_item.setExpanded(True)
        if not groups:
            self.status_label.setText(tr("backups.empty"))
        self._update_buttons()

    def selected(self) -> tuple[Backup, BackupGroup] | None:
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def selected_backup(self) -> Backup | None:
        selected = self.selected()
        return selected[0] if selected else None

    def _update_buttons(self) -> None:
        selected = self.selected()
        idle = self.task is None
        self.restore_button.setEnabled(idle and selected is not None and selected[1].target is not None)
        self.delete_button.setEnabled(idle and selected is not None)

    # --- Действия ---

    def restore_selected(self) -> None:
        selected = self.selected()
        if selected is None or selected[1].target is None:
            return
        backup, group = selected
        key = "backups.confirm_restore_delta" if backup.is_delta else "backups.confirm_restore"
        if not self.confirm(tr(key, world=backup.world_name)):
            return
        node, world_dir = self.node, group.target

        def work(task: Task) -> Path | None:
            return restore_backup(backup, world_dir, node.paths.tmp_dir, group.backups_root, node.settings.keep_backups)

        self.status_label.setText(tr("backups.restoring"))
        self._run(work, lambda _: self._after_action(tr("backups.restored", world=backup.world_name)))

    def delete_selected(self) -> None:
        backup = self.selected_backup()
        if backup is None or not self.confirm(tr("backups.confirm_delete")):
            return
        self._run(lambda task: delete_backup(backup), lambda _: self._after_action(tr("backups.deleted")))

    def _after_action(self, message: str) -> None:
        self.status_label.setText(message)
        self.reload()

    def confirm(self, text: str) -> bool:
        """Спросить «Да/Нет». Отдельный метод, чтобы в тестах подменить ответ."""
        box = QMessageBox(QMessageBox.Icon.Question, tr("backups.title"), text, parent=self)
        yes = set_variant(box.addButton(tr("common.yes"), QMessageBox.ButtonRole.YesRole), "primary")
        box.addButton(tr("common.no"), QMessageBox.ButtonRole.NoRole)
        box.exec()
        return box.clickedButton() is yes

    def _run(self, work: Callable[[Task], Any], on_done: Callable[[Any], None]) -> None:
        def finished(result: object) -> None:
            self.task = None
            on_done(result)
            self._update_buttons()

        def failed(error: BaseException) -> None:
            self.task = None
            self.status_label.setText(describe_error(error))
            self._update_buttons()

        self.task = run_task(work, on_done=finished, on_error=failed)
        self._update_buttons()
