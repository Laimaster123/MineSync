"""Полоска сборок: вкладки «Недавние», «26.3», «Modpack»…, а под ними — название и папка
выбранной сборки и с какой сборкой второго устройства она связана."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from blocksync.config.instances import Instance
from blocksync.net.protocol import RemoteInstance
from blocksync.ui import icons
from blocksync.ui.strings import tr
from blocksync.ui.theme import Colors
from blocksync.ui.widgets import Panel, icon_button, set_variant, with_icon

RECENT_TAB = "recent"

def peer_instance_name(instance_id: str, peer_instances: list[RemoteInstance] | None) -> str:
    """Название сборки второго устройства по ID (если список сборок ещё не пришёл — из самого ID)."""
    for instance in peer_instances or []:
        if instance.id == instance_id:
            return instance.name
    return instance_id.split(":", 1)[-1]


class InstanceBar(Panel):
    def __init__(
        self,
        on_select: Callable[[str], None],
        on_link: Callable[[str, str | None], None],
        on_refresh: Callable[[], None],
        on_menu: Callable[[QPushButton], None],
        on_add: Callable[[], None],
    ) -> None:
        super().__init__()
        self.on_select = on_select
        self.on_link = on_link
        self.tabs: dict[str, QPushButton] = {}
        self.current = RECENT_TAB

        self.tabs_layout = QHBoxLayout()
        self.tabs_layout.setSpacing(6)
        self.refresh_button = icon_button("sync", tr("main.refresh_button"))
        self.refresh_button.clicked.connect(on_refresh)
        self.menu_button = icon_button("dots", tr("instances.menu_tooltip"))
        self.menu_button.clicked.connect(lambda: on_menu(self.menu_button))
        self.loading_label = QLabel(tr("main.loading"))
        self.loading_label.setStyleSheet(f"color: {Colors.TEXT_DIM.name()};")
        self.loading_label.hide()
        self.add_button = icon_button("plus", tr("instances.add_tooltip"))
        self.add_button.clicked.connect(on_add)
        top = QHBoxLayout()
        top.addLayout(self.tabs_layout)
        top.addWidget(self.add_button)
        top.addStretch()
        top.addWidget(self.loading_label)
        top.addWidget(self.refresh_button)
        top.addWidget(self.menu_button)

        # Название и папка выбранной сборки.
        self.folder_icon = QLabel()
        self.folder_icon.setPixmap(icons.pixmap("folder", Colors.TEXT_DIM.name(), 24))
        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-weight: 600; font-size: 15px;")
        self.kind_label = QLabel()
        self.kind_label.setStyleSheet(f"color: {Colors.TEXT_DIM.name()}; font-size: 12px;")
        self.path_label = QLabel()
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setStyleSheet(
            f"color: {Colors.TEXT_DIM.name()}; font-family: Menlo, Consolas, monospace; font-size: 12px;"
        )
        names = QVBoxLayout()
        names.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self.name_label)
        title_row.addWidget(self.kind_label)
        title_row.addStretch()
        names.addLayout(title_row)
        names.addWidget(self.path_label)
        info = QHBoxLayout()
        info.setSpacing(12)
        info.addWidget(self.folder_icon)
        info.addLayout(names, 1)

        # Связь со сборкой второго устройства.
        self.link_icon = QLabel()
        self.link_label = QLabel()
        self.link_label.setWordWrap(True)
        self.link_combo = QComboBox()
        self.link_combo.setMinimumWidth(200)
        self.link_button = set_variant(QPushButton(tr("instances.link")), "primary")
        self.link_button.clicked.connect(self._link_clicked)
        self.change_button = QPushButton(tr("instances.change_link"))
        self.change_button.clicked.connect(self._show_chooser)
        self.unlink_button = QPushButton(tr("instances.unlink"))
        self.unlink_button.clicked.connect(lambda: self.on_link(self.current, None))
        self.link_row = QWidget()
        link_layout = QHBoxLayout(self.link_row)
        link_layout.setContentsMargins(0, 0, 0, 0)
        link_layout.setSpacing(10)
        link_layout.addWidget(self.link_icon)
        link_layout.addWidget(self.link_label, 1)
        link_layout.addWidget(self.link_combo)
        link_layout.addWidget(self.link_button)
        link_layout.addWidget(self.unlink_button)
        link_layout.addWidget(self.change_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addLayout(info)
        layout.addWidget(self.link_row)

    # --- Вкладки ---

    def set_tabs(self, instances: list[Instance], current: str) -> None:
        for button in self.tabs.values():
            button.deleteLater()
        self.tabs = {}
        recent = with_icon(QPushButton(), "clock", Colors.TEXT.name(), tr("instances.recent"))
        self._add_tab(RECENT_TAB, recent)
        for instance in instances:
            self._add_tab(instance.id, QPushButton(instance.name))
        self.select(current if current in self.tabs else RECENT_TAB)

    def _add_tab(self, tab: str, button: QPushButton) -> None:
        button.setCheckable(True)
        set_variant(button, "tab")
        button.clicked.connect(lambda: self.on_select(tab))
        self.tabs_layout.addWidget(button)
        self.tabs[tab] = button

    def select(self, tab: str) -> None:
        self.current = tab
        for key, button in self.tabs.items():
            button.setChecked(key == tab)

    # --- Сведения о сборке и связь ---

    def show_recent(self) -> None:
        self.name_label.setText(tr("instances.recent_title"))
        self.kind_label.setText("")
        self.path_label.setText(tr("instances.recent_hint"))
        self.path_label.setToolTip("")
        self.folder_icon.setPixmap(icons.pixmap("clock", Colors.TEXT_DIM.name(), 24))
        self.link_row.hide()

    def show_instance(
        self, instance: Instance, paired_name: str | None, online: bool,
        linked: str | None, peer_instances: list[RemoteInstance] | None,
    ) -> None:
        self.name_label.setText(instance.name)
        self.kind_label.setText(tr("instances.saves_caption"))
        self.path_label.setText(str(instance.saves_dir))
        self.path_label.setToolTip(tr("instances.folder_tooltip", folder=instance.folder))
        self.folder_icon.setPixmap(icons.pixmap("folder", Colors.TEXT_DIM.name(), 24))
        self._instance = instance
        self._paired_name = paired_name
        self._linked = linked
        self._peer_instances = peer_instances
        self.link_row.setVisible(paired_name is not None)
        if paired_name is None:
            return
        if linked is not None:
            self._show_linked()
        elif online and peer_instances is not None:
            self._show_chooser()
        else:
            self._set_link_text(tr("instances.wait_peer", peer=paired_name), Colors.TEXT_DIM, "link")
            self._show_controls(chooser=False, linked=False)

    def _show_linked(self) -> None:
        name = peer_instance_name(self._linked or "", self._peer_instances)
        self._set_link_text(tr("instances.linked", name=name, peer=self._paired_name), Colors.GREEN, "check")
        self._show_controls(chooser=False, linked=True)

    def _show_chooser(self) -> None:
        if not self._peer_instances:
            self._set_link_text(tr("instances.wait_peer", peer=self._paired_name), Colors.TEXT_DIM, "link")
            self._show_controls(chooser=False, linked=False)
            return
        self._set_link_text(
            tr("instances.choose", name=self._instance.name, peer=self._paired_name), Colors.YELLOW, "link"
        )
        self.link_combo.clear()
        for instance in self._peer_instances:
            self.link_combo.addItem(instance.name, instance.id)
        # Сначала предлагаем уже связанную сборку, иначе — сборку с таким же названием.
        preferred = self._linked or next(
            (i.id for i in self._peer_instances if i.name == self._instance.name), None
        )
        index = self.link_combo.findData(preferred)
        self.link_combo.setCurrentIndex(max(index, 0))
        self._show_controls(chooser=True, linked=self._linked is not None)

    def _show_controls(self, chooser: bool, linked: bool) -> None:
        self.link_combo.setVisible(chooser)
        self.link_button.setVisible(chooser)
        self.unlink_button.setVisible(chooser and linked)
        self.change_button.setVisible(not chooser and linked)

    def _set_link_text(self, text: str, color: object, icon_name: str) -> None:
        self.link_label.setText(text)
        self.link_label.setStyleSheet(f"color: {color.name()};")  # type: ignore[attr-defined]
        self.link_icon.setPixmap(icons.pixmap(icon_name, color.name(), 24))  # type: ignore[attr-defined]

    def _link_clicked(self) -> None:
        peer_instance = self.link_combo.currentData()
        if peer_instance:
            self.on_link(self.current, peer_instance)

    def set_loading(self, loading: bool) -> None:
        self.loading_label.setVisible(loading)
