"""Окно «Описание мира»: короткая подпись, чтобы не путать миры."""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from blocksync.net.node import MAX_NOTE_LENGTH
from blocksync.ui.strings import tr
from blocksync.ui.widgets import Dialog, Heading, set_variant


class NoteDialog(Dialog):
    def __init__(self, world_name: str, note: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("note.title"))
        self.setMinimumWidth(480)

        self.edit = QLineEdit(note)
        self.edit.setMaxLength(MAX_NOTE_LENGTH)
        self.edit.setPlaceholderText(tr("note.placeholder"))
        hint = QLabel(tr("note.hint"))
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")

        save = set_variant(QPushButton(tr("settings.save")), "primary")
        cancel = QPushButton(tr("common.cancel"))
        save.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        self.edit.returnPressed.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(Heading(tr("note.heading", world=world_name)))
        layout.addWidget(self.edit)
        layout.addWidget(hint)
        layout.addLayout(buttons)

    def note(self) -> str:
        return self.edit.text().strip()
