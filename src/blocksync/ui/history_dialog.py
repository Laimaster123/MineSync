"""Окно «История»: счётчики синхронизаций и список последних переносов."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from blocksync.config.history import Direction, SyncRecord, load_history, stats
from blocksync.net.node import Node
from blocksync.ui.errors import describe_error
from blocksync.ui.format import format_count, format_duration, format_size
from blocksync.ui.strings import tr
from blocksync.ui.theme import Colors, pixel_font
from blocksync.ui.widgets import Dialog, Panel
from blocksync.ui.worker import Task, run_task


class StatCard(Panel):
    """Карточка счётчика: крупное число и подпись."""

    def __init__(self, caption: str, color: str) -> None:
        super().__init__()
        self.value = QLabel("—")
        self.value.setFont(pixel_font(18))
        self.value.setStyleSheet(f"color: {color};")
        label = QLabel(caption)
        label.setProperty("role", "hint")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(self.value)
        layout.addWidget(label)


class HistoryDialog(Dialog):
    def __init__(self, node: Node, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.node = node
        self.setWindowTitle(tr("history.title"))
        self.resize(820, 540)

        self.received_card = StatCard(tr("history.received"), Colors.GREEN.name())
        self.sent_card = StatCard(tr("history.sent"), Colors.BLUE.name())
        self.bytes_card = StatCard(tr("history.bytes"), Colors.ACCENT.name())
        cards = QHBoxLayout()
        cards.setSpacing(12)
        for card in (self.received_card, self.sent_card, self.bytes_card):
            cards.addWidget(card)

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels([
            tr("history.column_when"), tr("history.column_world"),
            tr("history.column_direction"), tr("history.column_details"),
        ])
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in range(3):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        list_panel = Panel()
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(8, 8, 8, 8)
        list_layout.addWidget(self.tree)

        self.status_label = QLabel()
        self.status_label.setProperty("role", "hint")
        close = QPushButton(tr("common.close"))
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addLayout(cards)
        layout.addWidget(list_panel, 1)
        layout.addLayout(bottom)

        path = node.paths.history_file
        self.task: Task | None = run_task(lambda task: load_history(path), on_done=self._show, on_error=self._failed)

    def _show(self, records: list[SyncRecord]) -> None:
        self.task = None
        summary = stats(records)
        self.received_card.value.setText(str(summary.received))
        self.sent_card.value.setText(str(summary.sent))
        self.bytes_card.value.setText(format_size(summary.bytes_received))
        here = self.node.settings.device_name
        for record in records:
            if record.direction is Direction.RECEIVED:
                direction = tr("sync.direction", source=record.peer, target=here)
                details = tr(
                    "history.details_received",
                    copied=format_count(record.copied_files, "word.files"), size=format_size(record.bytes_copied),
                    deleted=format_count(record.deleted_files, "word.files"),
                    duration=format_duration(record.duration_seconds),
                )
            else:
                direction = tr("sync.direction", source=here, target=record.peer)
                details = tr("history.details_sent", peer=record.peer)
            item = QTreeWidgetItem([record.when.strftime("%d.%m.%Y %H:%M"), record.world, direction, details])
            item.setForeground(2, Colors.GREEN if record.direction is Direction.RECEIVED else Colors.BLUE)
            item.setForeground(3, Colors.TEXT_DIM)
            self.tree.addTopLevelItem(item)
        if records:
            self.status_label.setText(tr("history.total", count=format_count(len(records), "word.syncs")))
        else:
            self.status_label.setText(tr("history.empty"))

    def _failed(self, error: BaseException) -> None:
        self.task = None
        self.status_label.setText(describe_error(error))
