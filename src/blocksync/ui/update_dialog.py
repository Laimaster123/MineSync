"""Окно «Доступна новая версия»: что нового и кнопка скачать."""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from blocksync import __version__
from blocksync.net.node import Node
from blocksync.net.updates import Release
from blocksync.ui.strings import tr
from blocksync.ui.theme import Colors
from blocksync.ui.widgets import Dialog, Heading, set_variant

# Длинный текст выпуска обрезаем: подробности есть на странице выпуска.
MAX_NOTES_LENGTH = 2000


class UpdateDialog(Dialog):
    def __init__(self, node: Node, release: Release, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.node = node
        self.release = release
        self.setWindowTitle(tr("update.title"))
        self.setMinimumWidth(560)

        heading = Heading(tr("update.heading", version=release.version), 18, Colors.GREEN)
        current = QLabel(tr("update.current", version=__version__))
        current.setProperty("role", "hint")

        notes = release.notes.strip()
        if len(notes) > MAX_NOTES_LENGTH:
            notes = notes[:MAX_NOTES_LENGTH].rstrip() + "…"
        self.notes_label = QLabel(notes or tr("update.no_notes"))
        self.notes_label.setTextFormat(Qt.TextFormat.MarkdownText)
        self.notes_label.setWordWrap(True)
        self.notes_label.setOpenExternalLinks(True)
        scroll = QScrollArea()
        scroll.setWidget(self.notes_label)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(260)

        hint = QLabel(tr("update.hint"))
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")

        self.skip_button = QPushButton(tr("update.skip"))
        self.skip_button.clicked.connect(self.skip)
        later = QPushButton(tr("update.later"))
        later.clicked.connect(self.reject)
        self.download_button = set_variant(QPushButton(tr("update.download")), "primary")
        self.download_button.clicked.connect(self.download)
        buttons = QHBoxLayout()
        buttons.addWidget(self.skip_button)
        buttons.addStretch()
        buttons.addWidget(later)
        buttons.addWidget(self.download_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(heading)
        layout.addWidget(current)
        layout.addWidget(scroll)
        layout.addWidget(hint)
        layout.addLayout(buttons)

    def download(self) -> None:
        """Открыть ссылку на архив новой версии: браузер скачает его в «Загрузки»."""
        QDesktopServices.openUrl(QUrl(self.release.download_url))
        self.accept()

    def skip(self) -> None:
        """Не напоминать об этой версии (о следующей — напомнить)."""
        self.node.skip_version(self.release.version)
        self.reject()
