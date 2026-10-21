"""Список миров: строка на каждый мир с иконкой, сведениями, статусом и своей кнопкой «Синхронизировать»."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from blocksync.net.overview import WorldRow, WorldStatus
from blocksync.ui.format import format_play_time, format_size, format_time
from blocksync.ui.strings import tr
from blocksync.ui.theme import Colors, rgba, text_font
from blocksync.ui.widgets import Panel, StatusPill, icon_button, set_variant, with_icon

ICON_SIZE = 52

# Ширина столбцов (пикселей), одинаковая у заголовка и у строк, чтобы всё стояло ровно.
VERSION_WIDTH = 64
SIZE_WIDTH = 72
MODIFIED_WIDTH = 120
STATUS_WIDTH = 150
ACTION_WIDTH = 206
MENU_WIDTH = 36

STATUS_LOOK: dict[WorldStatus, tuple[str, str, QColor]] = {
    # статус: (ключ текста, значок, цвет)
    WorldStatus.SAME: ("status.same", "check", Colors.GREEN),
    WorldStatus.DIFFERENT: ("status.different", "warning", Colors.YELLOW),
    WorldStatus.ONLY_HERE: ("status.only_here", "monitor", Colors.BLUE),
    WorldStatus.ONLY_THERE: ("status.only_there", "monitor", Colors.BLUE),
    WorldStatus.LOCKED: ("status.locked", "lock", Colors.RED),
    WorldStatus.UNKNOWN: ("status.unknown", "question", Colors.GRAY),
    WorldStatus.NOT_LINKED: ("status.not_linked", "link", Colors.GRAY),
}

# Миры с этими статусами есть смысл синхронизировать: кнопка горит зелёным.
SYNCABLE = {WorldStatus.DIFFERENT, WorldStatus.ONLY_HERE, WorldStatus.ONLY_THERE}

DISABLED_REASON_KEYS = {
    WorldStatus.SAME: "main.sync_disabled_same",
    WorldStatus.LOCKED: "main.sync_disabled_locked",
    WorldStatus.UNKNOWN: "main.sync_disabled_offline",
    WorldStatus.NOT_LINKED: "main.sync_disabled_not_linked",
}


GAME_TYPE_KEYS = {0: "badge.survival", 1: "badge.creative", 2: "badge.adventure", 3: "badge.spectator"}


def badges(row: WorldRow) -> list[tuple[str, QColor]]:
    """Значки мира из level.dat: режим игры, «Хардкор», время в игре."""
    info = row.info
    result: list[tuple[str, QColor]] = []
    if info.hardcore:
        result.append((tr("badge.hardcore"), Colors.RED))
    elif info.game_type in GAME_TYPE_KEYS:
        result.append((tr(GAME_TYPE_KEYS[info.game_type]), Colors.GREEN if info.game_type == 0 else Colors.BLUE))
    if info.play_ticks:
        result.append((format_play_time(info.play_ticks), Colors.TEXT_DIM))
    return result


def _badge(text: str, color: QColor) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        f"color: {color.name()}; background: {rgba(color, 0.12)}; border-radius: 6px;"
        f" padding: 1px 6px; font-size: 11px; font-weight: 600;"
    )
    return label


def _dates(modified_ns: int | None, synced: datetime | None) -> QWidget:
    """Дата изменения, а под ней мелко — когда мир последний раз синхронизировали."""
    box = QWidget()
    box.setFixedWidth(MODIFIED_WIDTH)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addStretch()
    layout.addWidget(_cell(format_time(modified_ns), MODIFIED_WIDTH))
    if synced is not None:
        label = _cell(tr("main.synced_at", time=synced.strftime("%d.%m %H:%M")), MODIFIED_WIDTH)
        label.setStyleSheet(f"color: {Colors.GREEN.name()}; font-size: 11px;")
        layout.addWidget(label)
    layout.addStretch()
    return box


def _cell(text: str, width: int, dim: bool = True) -> QLabel:
    label = QLabel(text)
    label.setFixedWidth(width)
    if dim:
        label.setStyleSheet(f"color: {Colors.TEXT_DIM.name()};")
    return label


class WorldRowWidget(QFrame):
    """Одна строка списка."""

    def __init__(
        self, row: WorldRow, icon: QPixmap, note: str | None, actions: "RowActions", show_instance: bool,
        synced: datetime | None = None,
    ) -> None:
        super().__init__()
        self.row = row
        self.note = note
        self.setObjectName("worldRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

        icon_label = QLabel()
        icon_label.setPixmap(icon)
        icon_label.setFixedSize(ICON_SIZE, ICON_SIZE)

        name = QLabel(row.display_name)
        name.setFont(text_font(15, bold=True))
        # Длинное название или описание не должно раздвигать остальные столбцы.
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setToolTip(row.display_name)
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addStretch()
        names.addWidget(name)
        # Под названием — значки (режим, хардкор, время в игре) и описание мира,
        # а если описания нет — имя папки (когда оно отличается от названия).
        details = QHBoxLayout()
        details.setSpacing(6)
        if show_instance:
            details.addWidget(_badge(row.instance_name, Colors.ACCENT))
        for text, color in badges(row):
            details.addWidget(_badge(text, color))
        subtitle = note or (tr("main.folder_subtitle", folder=row.folder_name)
                            if row.display_name != row.folder_name else "")
        if subtitle:
            self.subtitle = QLabel(subtitle)
            self.subtitle.setStyleSheet(f"color: {Colors.TEXT_DIM.name()}; font-size: 12px;")
            self.subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.subtitle.setToolTip(subtitle)
            details.addSpacing(4)
            details.addWidget(self.subtitle, 1)
        else:
            details.addStretch()
        names.addLayout(details)
        names.addStretch()

        text, icon_name, color = STATUS_LOOK[row.status]
        pill = StatusPill()
        pill.set_status(tr(text), icon_name, color)
        pill_box = QWidget()
        pill_box.setFixedWidth(STATUS_WIDTH)
        pill_layout = QHBoxLayout(pill_box)
        pill_layout.setContentsMargins(0, 0, 0, 0)
        pill_layout.addWidget(pill)
        pill_layout.addStretch()

        self.sync_button = QPushButton(tr("main.sync_row"))
        self.sync_button.setFixedWidth(ACTION_WIDTH)
        self.sync_button.clicked.connect(lambda: actions.sync(row))

        # Меню «…»: описание мира и папка мира.
        self.menu = QMenu(self)
        self.menu.addAction(tr("main.menu_edit_note"), lambda: actions.edit_note(row))
        if row.local is not None:
            self.menu.addAction(tr("main.menu_open_folder"), lambda: actions.open_folder(row))
        self.menu_button = icon_button("dots", tr("main.menu_tooltip"))
        self.menu_button.clicked.connect(
            lambda: self.menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)
        layout.addWidget(icon_label)
        layout.addLayout(names, 1)
        layout.addWidget(_cell(row.version_name or "—", VERSION_WIDTH))
        layout.addWidget(_cell(format_size(row.size_bytes), SIZE_WIDTH))
        layout.addWidget(_dates(row.last_modified_ns, synced))
        layout.addWidget(pill_box)
        layout.addWidget(self.sync_button)
        layout.addWidget(self.menu_button)

    def set_can_sync(self, online: bool, busy: bool) -> None:
        """Зелёная кнопка — миры отличаются и синхронизировать можно; серая — нельзя или незачем."""
        status = self.row.status
        enabled = online and not busy and status in SYNCABLE
        self.sync_button.setEnabled(enabled)
        set_variant(self.sync_button, "primary" if enabled else None)
        with_icon(self.sync_button, "sync", "#ffffff" if enabled else Colors.GRAY.name(), tr("main.sync_row"))
        if enabled:
            self.sync_button.setToolTip("")
        elif not online:
            self.sync_button.setToolTip(tr("main.sync_disabled_offline"))
        elif busy:
            self.sync_button.setToolTip(tr("main.sync_disabled_busy"))
        else:
            self.sync_button.setToolTip(tr(DISABLED_REASON_KEYS[status]))


@dataclass(frozen=True)
class RowActions:
    """Что делать по кнопкам строки; каждой функции передаётся строка списка."""

    sync: Callable[[WorldRow], None]
    edit_note: Callable[[WorldRow], None]
    open_folder: Callable[[WorldRow], None]


class WorldList(Panel):
    """Панель со списком миров: строка заголовков и прокручиваемые строки."""

    def __init__(self, actions: RowActions) -> None:
        super().__init__()
        self.actions = actions
        self.rows: dict[str, WorldRowWidget] = {}

        header = QHBoxLayout()
        header.setContentsMargins(14 + ICON_SIZE + 14, 12, 14, 10)
        header.setSpacing(14)
        header.addWidget(self._header_cell(tr("main.column_name")), 1)
        for key, width in (
            ("main.column_version", VERSION_WIDTH), ("main.column_size", SIZE_WIDTH),
            ("main.column_modified", MODIFIED_WIDTH), ("main.column_status", STATUS_WIDTH), ("", ACTION_WIDTH + 14 + MENU_WIDTH),
        ):
            header.addWidget(self._header_cell(tr(key) if key else "", width))
        separator = QFrame()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background: {Colors.PANEL_BORDER.name()}; border: none;")

        self.rows_box = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_box)
        self.rows_layout.setContentsMargins(6, 6, 6, 6)
        self.rows_layout.setSpacing(2)
        self.rows_layout.addStretch(1)  # свободное место — под строками, а не между ними
        self.scroll = scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.rows_box)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(separator)
        layout.addWidget(scroll, 1)

        self.setStyleSheet(f"""
            QFrame#worldRow {{ background: transparent; border: none; border-radius: 10px; }}
            QFrame#worldRow:hover {{ background: {Colors.ROW_HOVER.name()}; }}
        """)

    @staticmethod
    def _header_cell(text: str, width: int | None = None) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {Colors.TEXT_DIM.name()}; font-size: 13px;")
        if width is not None:
            label.setFixedWidth(width)
        return label

    def set_rows(
        self, rows: list[WorldRow], icons_by_world: dict[str, QPixmap], notes: dict[str, str],
        show_instance: bool = False, synced: dict[str, datetime] | None = None,
    ) -> None:
        """icons_by_world, notes и synced (когда синхронизирован) — по ключу строки (WorldRow.key)."""
        # Строки пересоздаются — запоминаем прокрутку, чтобы список не прыгал.
        bar = self.scroll.verticalScrollBar()
        position = bar.value()
        synced = synced or {}
        for widget in self.rows.values():
            # Убираем из списка сразу; сам виджет Qt удалит чуть позже (deleteLater).
            self.rows_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self.rows = {}
        for index, row in enumerate(rows):
            widget = WorldRowWidget(
                row, icons_by_world[row.key], notes.get(row.key), self.actions, show_instance, synced.get(row.key)
            )
            self.rows_layout.insertWidget(index, widget)
            self.rows[row.key] = widget
        # Высота списка пересчитается чуть позже — тогда и вернём прокрутку.
        QTimer.singleShot(0, bar, lambda: bar.setValue(position))  # отменится, если окно уже закрыли

    def row_for(self, folder: str) -> WorldRowWidget:
        """Строка мира по имени папки (первая, если мир есть в нескольких сборках)."""
        return next(widget for widget in self.rows.values() if widget.row.folder_name == folder)

    def update_buttons(self, online: bool, busy: bool) -> None:
        for widget in self.rows.values():
            widget.set_can_sync(online, busy)
