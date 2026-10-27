"""Главное окно: логотип и статус второго устройства, полоска сборок, список миров с кнопками синхронизации."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from blocksync import __version__
from blocksync.config.history import SyncRecord, last_synced, load_history
from blocksync.config.instances import Instance, InstanceError, InstanceProblem
from blocksync.core.world import list_worlds
from blocksync.net.client import NetworkError
from blocksync.net.discovery import PeerStatus
from blocksync.net.node import Node
from blocksync.net.overview import WorldRow, build_overview, load_icons
from blocksync.net.protocol import RemoteInstance, SyncRequest
from blocksync.ui import icons
from blocksync.ui.backups_dialog import BackupsDialog
from blocksync.ui.change_watcher import WorldWatcher
from blocksync.ui.errors import describe_error
from blocksync.ui.history_dialog import HistoryDialog
from blocksync.ui.instance_bar import RECENT_TAB, InstanceBar
from blocksync.ui.note_dialog import NoteDialog
from blocksync.ui.pairing_dialog import PairingDialog
from blocksync.ui.settings_dialog import SettingsDialog
from blocksync.ui.strings import LANGUAGES, current_language, set_language, tr
from blocksync.ui.sync_dialog import SyncDialog
from blocksync.ui.theme import Colors, rgba
from blocksync.ui.widgets import (
    AchievementToast,
    Background,
    PixelText,
    StatusDot,
    default_world_icon,
    iso_block,
    logo_icon,
    set_variant,
    with_icon,
    world_icon_from_png,
)
from blocksync.ui.world_list import ICON_SIZE, RowActions, WorldList
from blocksync.ui.worker import NodeBridge, Task, run_task

logger = logging.getLogger(__name__)

# Обновлять список не чаще, чем раз в эту паузу после события из сети (мс):
# при появлении устройства события приходят пачкой.
REFRESH_DELAY_MS = 300
# Сколько миров показывать на вкладке «Недавние».
RECENT_LIMIT = 8

INSTANCE_PROBLEM_KEYS = {
    InstanceProblem.NOT_A_FOLDER: "instances.error_not_a_folder",
    InstanceProblem.IS_WORLD: "instances.error_is_world",
    InstanceProblem.NO_SAVES: "instances.error_no_saves",
    InstanceProblem.ALREADY_ADDED: "instances.error_already_added",
}


@dataclass
class View:
    """Всё, что нужно показать на выбранной вкладке (собирается в фоне)."""

    instances: list[Instance]
    peer_instances: list[RemoteInstance] | None
    tab: str = RECENT_TAB  # для какой вкладки собрано
    rows: list[WorldRow] = field(default_factory=list)
    icons: dict[str, bytes] = field(default_factory=dict)  # по WorldRow.key
    saves_missing: bool = False
    peer_error: NetworkError | None = None
    synced: dict[str, datetime] = field(default_factory=dict)  # когда мир синхронизирован, по WorldRow.key


def _instance_rows(
    node: Node, instance: Instance
) -> tuple[list[WorldRow], dict[str, bytes], bool, NetworkError | None]:
    """Миры одной сборки, сравнённые со связанной сборкой второго устройства."""
    remote = node.linked_instance(instance.id)
    client = node.peer_client(remote) if remote is not None else None
    if client is None:
        overview = build_overview(instance.saves_dir, None, instance.id, instance.name, linked=remote is not None)
        return overview.rows, _keyed(load_icons(overview.rows, None), overview.rows), overview.saves_missing, None
    with client:
        overview = build_overview(instance.saves_dir, client, instance.id, instance.name)
        icon_data = load_icons(overview.rows, client)
    return overview.rows, _keyed(icon_data, overview.rows), overview.saves_missing, overview.peer_error


def _keyed(by_folder: dict[str, bytes], rows: list[WorldRow]) -> dict[str, bytes]:
    return {row.key: by_folder[row.folder_name] for row in rows if row.folder_name in by_folder}


def _recent_rows(node: Node, instances: list[Instance]) -> View:
    """Недавние миры из всех сборок этого компьютера."""
    latest: list[tuple[int, Instance, str]] = []
    for instance in instances:
        try:
            worlds = list_worlds(instance.saves_dir)
        except FileNotFoundError:
            continue
        latest += [(w.last_modified_ns or 0, instance, w.folder_name) for w in worlds]
    latest.sort(key=lambda item: item[0], reverse=True)
    wanted: dict[str, set[str]] = {}
    for _, instance, folder in latest[:RECENT_LIMIT]:
        wanted.setdefault(instance.id, set()).add(folder)

    view = View(instances, None)
    for instance in instances:
        if instance.id not in wanted:
            continue
        rows, icon_data, _, peer_error = _instance_rows(node, instance)
        rows = [row for row in rows if row.local is not None and row.folder_name in wanted[instance.id]]
        view.rows += rows
        view.icons.update({row.key: icon_data[row.key] for row in rows if row.key in icon_data})
        view.peer_error = view.peer_error or peer_error
    view.rows.sort(key=lambda row: row.local.last_modified_ns or 0 if row.local else 0, reverse=True)
    return view


def _sort_by_sync(view: View, records: list[SyncRecord]) -> None:
    """Сверху — недавно синхронизированные миры, ниже — давно, внизу — ни разу (по дате изменения)."""
    for row in view.rows:
        when = last_synced(records, row.instance_id, row.folder_name, row.display_name)
        if when is not None:
            view.synced[row.key] = when

    def order(row: WorldRow) -> tuple[bool, float]:
        when = view.synced.get(row.key)
        if when is not None:
            return False, -when.timestamp()
        return True, -((row.last_modified_ns or 0) / 1e9)

    view.rows.sort(key=order)


class MainWindow(QMainWindow):
    def __init__(self, node: Node, bridge: NodeBridge) -> None:
        super().__init__()
        self.node = node
        self.bridge = bridge
        self.view: View | None = None
        self.instances: list[Instance] = []
        self.peer_instances: list[RemoteInstance] | None = None
        self.tab = node.settings.selected_tab or RECENT_TAB
        self._refresh_task: Task | None = None
        self._refresh_again = False
        self.sync_dialog: SyncDialog | None = None

        set_language(node.settings.language)
        self.setWindowTitle("MineSync")
        self.setWindowIcon(logo_icon())
        self.resize(1180, 760)
        self._build()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_DELAY_MS)
        self._refresh_timer.timeout.connect(self.refresh)
        bridge.changed.connect(self._on_node_changed)
        bridge.sync_requested.connect(self._on_sync_requested)
        # Сам замечает сохранения миров и вход/выход из игры (без нагрузки на диск).
        self.watcher = WorldWatcher(self)
        self.watcher.changed.connect(self._on_worlds_changed)

        self.refresh()

    # --- Построение окна ---

    def _build(self) -> None:
        """Создать все виджеты окна (вызывается и при смене языка)."""
        logo = QLabel()
        logo.setPixmap(iso_block(48))
        self.peer_dot = StatusDot()
        self.peer_label = QLabel()
        self.peer_label.setStyleSheet("font-weight: 600;")
        self.peer_pill = QFrame()
        pill_layout = QHBoxLayout(self.peer_pill)
        pill_layout.setContentsMargins(10, 4, 16, 4)
        pill_layout.addWidget(self.peer_dot)
        pill_layout.addWidget(self.peer_label)

        self.pair_button = self._header_button("link", tr("main.pairing_button"), self.open_pairing)
        self.history_button = self._header_button("clock", tr("main.history_button"), self.open_history)
        self.backups_button = self._header_button("backups", tr("main.backups_button"), self.open_backups)
        self.settings_button = self._header_button("gear", tr("main.settings_button"), self.open_settings)
        self.language_button = QPushButton()
        self.language_button.setIcon(QIcon(icons.flag(current_language())))
        self.language_button.setToolTip(tr("main.language_tooltip"))
        self.language_button.clicked.connect(self._show_language_menu)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(logo)
        header.addWidget(PixelText("MineSync", 26))
        header.addSpacing(10)
        header.addWidget(self.peer_pill)
        header.addStretch()
        for button in (self.pair_button, self.history_button, self.backups_button, self.settings_button,
                       self.language_button):
            header.addWidget(button)

        self.instance_bar = InstanceBar(
            self.select_tab, self.link_instance, self.refresh, self._show_folder_menu, self.add_instance
        )

        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet(f"color: {Colors.YELLOW.name()};")
        self.message_label.hide()

        self.world_list = WorldList(RowActions(self.start_sync, self.edit_note, self.open_world_folder))

        hint = QLabel(tr("main.hint"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {Colors.TEXT_DIM.name()};")

        layout = QVBoxLayout()
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(14)
        layout.addLayout(header)
        layout.addWidget(self.instance_bar)
        layout.addWidget(self.message_label)
        layout.addWidget(self.world_list, 1)
        layout.addWidget(hint)
        version = QLabel(tr("main.version", version=__version__))
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setStyleSheet(f"color: {rgba(Colors.TEXT_DIM, 0.45)}; font-size: 11px;")
        layout.addSpacing(16)  # место для украшений из блоков в нижних углах
        layout.addWidget(version)
        layout.addSpacing(6)
        central = Background()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.toast = AchievementToast(central)
        self._update_peer_label()

    def _header_button(self, icon_name: str, text: str, slot: object) -> QPushButton:
        button = with_icon(QPushButton(), icon_name, Colors.TEXT.name(), text)
        button.clicked.connect(slot)
        return button

    # --- Обновление ---

    def refresh(self) -> None:
        """Перечитать сборки и миры выбранной вкладки на обоих устройствах (в фоне)."""
        if self._refresh_task is not None:
            self._refresh_again = True  # обновим ещё раз, когда закончится текущее
            return
        self.instance_bar.set_loading(True)
        node, tab = self.node, self.tab

        def work(task: Task) -> View:
            instances = node.instances()
            peer_instances = None
            client = node.peer_client()
            if client is not None:
                with client:
                    try:
                        peer_instances = client.list_instances()
                    except NetworkError as error:
                        logger.info("Не удалось получить сборки второго устройства: %s", error)
            if tab == RECENT_TAB:
                view = _recent_rows(node, instances)
                view.peer_instances = peer_instances
                return view
            instance = next((i for i in instances if i.id == tab), None)
            view = View(instances, peer_instances, tab)
            if instance is not None:
                view.rows, view.icons, view.saves_missing, view.peer_error = _instance_rows(node, instance)
                _sort_by_sync(view, load_history(node.paths.history_file))
            return view

        self._refresh_task = run_task(work, on_done=self._show_view, on_error=self._refresh_failed)

    def _show_view(self, view: View) -> None:
        self._refresh_finished()
        if view.tab != self.tab:
            # Пока список собирался, пользователь открыл другую вкладку: этот результат устарел.
            self.refresh()
            return
        self.view = view
        self.instances = view.instances
        self.peer_instances = view.peer_instances
        if self.tab != RECENT_TAB and self._instance(self.tab) is None:
            self.tab = RECENT_TAB  # сборку удалили из лаунчера
        self.instance_bar.set_tabs(self.instances, self.tab)
        self._update_instance_info()

        pictures: dict[str, QPixmap] = {}
        for row in view.rows:
            data = view.icons.get(row.key)
            picture = world_icon_from_png(data, ICON_SIZE) if data else None
            pictures[row.key] = picture or default_world_icon(ICON_SIZE)
        self.world_list.set_rows(
            view.rows, pictures, self._notes(view.rows), show_instance=self.tab == RECENT_TAB, synced=view.synced
        )

        messages = []
        if not self.instances:
            messages.append(tr("instances.empty"))
        elif view.saves_missing:
            instance = self._instance(self.tab)
            messages.append(tr("main.saves_missing", path=instance.saves_dir if instance else ""))
        elif not view.rows:
            messages.append(tr("main.no_worlds"))
        if view.peer_error:
            messages.append(tr("main.peer_error", error=describe_error(view.peer_error)))
        self._show_message(messages)
        self._update_sync_button()
        self._watch(view)

    def _watch(self, view: View) -> None:
        """Следить за папками миров, которые сейчас на экране, и за папками saves (новые миры)."""
        shown = view.instances if self.tab == RECENT_TAB else [i for i in view.instances if i.id == self.tab]
        folders = [instance.saves_dir for instance in shown]
        locks = {row.local.path: row.local.is_locked for row in view.rows if row.local is not None}
        self.watcher.watch(folders + list(locks), locks)

    def _on_worlds_changed(self) -> None:
        """В мирах этого компьютера что-то изменилось: обновить список и сообщить второму устройству."""
        self.refresh()
        node = self.node
        run_task(lambda task: node.notify_peer_changed())

    def _update_instance_info(self) -> None:
        instance = self._instance(self.tab)
        if instance is None:
            self.instance_bar.show_recent()
            return
        paired = self.node.paired
        self.instance_bar.show_instance(
            instance, paired.name if paired else None, self.node.status() is PeerStatus.ONLINE,
            self.node.linked_instance(instance.id), self.peer_instances,
        )

    def _notes(self, rows: list[WorldRow]) -> dict[str, str]:
        """Описания миров: своё, а если его нет — сделанное на другом устройстве."""
        local = self.node.world_notes()
        notes: dict[str, str] = {}
        for row in rows:
            note = local.get(row.folder_name) or (row.remote.note if row.remote else None)
            if note:
                notes[row.key] = note
        return notes

    def _refresh_failed(self, error: BaseException) -> None:
        self._refresh_finished()
        self._show_message([describe_error(error)])

    def _refresh_finished(self) -> None:
        self._refresh_task = None
        self.instance_bar.set_loading(False)
        if self._refresh_again:
            self._refresh_again = False
            self._refresh_timer.start()

    def _show_message(self, messages: list[str]) -> None:
        self.message_label.setText("\n".join(messages))
        self.message_label.setVisible(bool(messages))

    # --- Состояние ---

    def _instance(self, instance_id: str) -> Instance | None:
        return next((i for i in self.instances if i.id == instance_id), None)

    def _update_peer_label(self) -> None:
        paired = self.node.paired
        status = self.node.status()
        if status is PeerStatus.NOT_PAIRED or paired is None:
            text, color = tr("main.peer_not_paired"), Colors.GRAY
        elif status is PeerStatus.ONLINE:
            text, color = tr("main.peer_online", name=paired.name), Colors.GREEN
        else:
            text, color = tr("main.peer_offline", name=paired.name), Colors.RED
        self.peer_label.setText(text)
        self.peer_dot.set_color(color)
        self.peer_pill.setStyleSheet(
            f"QFrame {{ background: {rgba(color, 0.10)}; border: 1px solid {rgba(color, 0.6)};"
            f" border-radius: 16px; }} QLabel {{ background: transparent; border: none; }}"
        )

    def _update_sync_button(self) -> None:
        online = self.node.status() is PeerStatus.ONLINE
        self.world_list.update_buttons(online, busy=self.sync_dialog is not None)

    def _on_node_changed(self) -> None:
        self._update_peer_label()
        self._update_sync_button()
        self._refresh_timer.start()

    # --- Сборки ---

    def select_tab(self, tab: str) -> None:
        self.tab = tab
        self.instance_bar.select(tab)
        self.node.select_tab(tab)
        self._update_instance_info()
        self.world_list.set_rows([], {}, {})
        self.refresh()

    def link_instance(self, instance_id: str, peer_instance: str | None) -> None:
        """Связать сборку со сборкой второго устройства (запоминается на обоих устройствах)."""
        node = self.node
        self.instance_bar.set_loading(True)
        run_task(
            lambda task: node.link_instance(instance_id, peer_instance),
            on_done=lambda _: self.refresh(),
            on_error=lambda error: (self._show_message([describe_error(error)]), self.refresh()),
        )

    def _show_folder_menu(self, button: QPushButton) -> None:
        menu = QMenu(self)
        instance = self._instance(self.tab)
        menu.addAction(tr("instances.add"), self.add_instance)
        if instance is not None:
            menu.addAction(tr("instances.open_folder"), lambda: self._open_path(instance.saves_dir))
            menu.addAction(tr("instances.remove", name=instance.name), lambda: self.remove_instance(instance))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def add_instance(self) -> None:
        """Кнопка «+»: выбрать папку сборки; папку с мирами программа найдёт внутри сама."""
        folder = QFileDialog.getExistingDirectory(self, tr("instances.choose_title"), str(Path.home()))
        if not folder:
            return
        node = self.node
        self.instance_bar.set_loading(True)
        run_task(
            lambda task: node.add_instance(Path(folder)),
            on_done=lambda instance: self.select_tab(instance.id),
            on_error=self._add_instance_failed,
        )

    def _add_instance_failed(self, error: BaseException) -> None:
        self.instance_bar.set_loading(False)
        if isinstance(error, InstanceError):
            if error.problem is InstanceProblem.ALREADY_ADDED and error.existing is not None:
                self.select_tab(error.existing.id)
            text = tr(INSTANCE_PROBLEM_KEYS[error.problem], folder=error.folder,
                      name=error.existing.name if error.existing else "")
        else:
            text = describe_error(error)
        self.show_warning(tr("instances.add_failed_title"), text)

    def remove_instance(self, instance: Instance) -> None:
        if not self.confirm(tr("instances.confirm_remove", name=instance.name)):
            return
        node = self.node
        run_task(
            lambda task: node.remove_instance(instance.id),
            on_done=lambda _: self.select_tab(RECENT_TAB),
            on_error=lambda error: self._show_message([describe_error(error)]),
        )

    def confirm(self, text: str) -> bool:
        """Спросить «Да/Нет». Отдельный метод, чтобы в тестах подменить ответ."""
        box = QMessageBox(QMessageBox.Icon.Question, "MineSync", text, parent=self)
        yes = set_variant(box.addButton(tr("common.yes"), QMessageBox.ButtonRole.YesRole), "primary")
        box.addButton(tr("common.no"), QMessageBox.ButtonRole.NoRole)
        box.exec()
        return box.clickedButton() is yes

    def show_warning(self, title: str, text: str) -> None:
        """Сообщение об ошибке с кнопкой «Понятно». Отдельный метод — чтобы тесты могли его перехватить."""
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        box.setStyleSheet("QLabel { min-width: 460px; }")
        set_variant(box.addButton(tr("common.ok"), QMessageBox.ButtonRole.AcceptRole), "primary")
        box.exec()

    # --- Язык ---

    def _show_language_menu(self) -> None:
        menu = QMenu(self)
        for language in LANGUAGES:
            menu.addAction(QIcon(icons.flag(language)), tr(f"language.{language}"),
                           lambda language=language: self.change_language(language))
        menu.exec(self.language_button.mapToGlobal(self.language_button.rect().bottomLeft()))

    def change_language(self, language: str) -> None:
        """Сменить язык сразу: окно пересоздаёт свои виджеты с новыми текстами."""
        if language == current_language():
            return
        self.node.set_language(language)
        set_language(language)
        self._build()
        if self.view is not None:
            self._show_view(self.view)

    # --- Миры ---

    def start_sync(self, row: WorldRow) -> None:
        """Нажата кнопка «Синхронизировать» у мира."""
        instance = self._instance(row.instance_id)
        remote = self.node.linked_instance(row.instance_id)
        if instance is None or remote is None or self.sync_dialog is not None:
            return
        if self.node.status() is not PeerStatus.ONLINE:
            return
        self._open_sync_dialog(SyncDialog(
            self.node, row.folder_name, row.display_name, instance.saves_dir, remote, instance.id, parent=self,
        ))

    def _on_sync_requested(self, request: SyncRequest) -> None:
        """Второе устройство просит забрать у него мир (там выбрали «играл на нём»)."""
        world = request.world
        if self.sync_dialog is not None:
            logger.warning("Просьба забрать мир %s пришла во время другой синхронизации", world)
            self.statusBar().showMessage(tr("main.sync_request_busy", world=world), 10_000)
            return
        target = self.node.instance(request.target_instance) if request.target_instance else None
        saves_dir = target.saves_dir if target is not None else self.node.saves_dir
        row = next((r for r in self.view.rows if r.folder_name == world), None) if self.view else None
        display_name = row.display_name if row else world
        self._open_sync_dialog(SyncDialog(
            self.node, world, display_name, saves_dir, request.instance,
            target.id if target else None, parent=self, incoming=True,
        ))

    def edit_note(self, row: WorldRow) -> None:
        """Меню «…» → «Изменить описание»."""
        current = self._notes([row]).get(row.key, "")
        dialog = NoteDialog(row.display_name, current, parent=self)
        if dialog.exec():
            self.node.set_world_note(row.folder_name, dialog.note())
            self.refresh()

    def open_world_folder(self, row: WorldRow) -> None:
        """Меню «…» → «Показать папку мира» (в Finder или Проводнике)."""
        instance = self._instance(row.instance_id)
        if instance is not None:
            self._open_path(instance.saves_dir / row.folder_name)

    @staticmethod
    def _open_path(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_sync_dialog(self, dialog: SyncDialog) -> None:
        self.sync_dialog = dialog
        self.watcher.paused = True
        dialog.synced.connect(self._celebrate)
        self._update_sync_button()
        dialog.finished.connect(self._sync_dialog_closed)
        dialog.open()

    def _celebrate(self, world: str, source: str) -> None:
        """Мир перенесён — показываем «достижение»."""
        self.toast.show_message(tr("toast.title"), tr("toast.text", world=world, source=source))

    def _sync_dialog_closed(self) -> None:
        self.sync_dialog = None
        self.watcher.paused = False
        self._update_sync_button()
        self._on_worlds_changed()

    # --- Окна ---

    def open_pairing(self) -> None:
        PairingDialog(self.node, self.bridge, parent=self).exec()
        self._on_node_changed()

    def open_history(self) -> None:
        HistoryDialog(self.node, parent=self).exec()

    def open_backups(self) -> None:
        BackupsDialog(self.node, parent=self).exec()
        self.refresh()

    def open_settings(self) -> None:
        if SettingsDialog(self.node, parent=self).exec():
            self.refresh()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.sync_dialog is not None:
            self.sync_dialog.cancel()
        super().closeEvent(event)

