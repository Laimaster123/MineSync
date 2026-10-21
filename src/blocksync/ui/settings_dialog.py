"""Окно «Настройки»: имя устройства, число бэкапов, папка бэкапов (сборки добавляются кнопкой «+»)."""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from blocksync.core.backups import MAX_BACKUPS, MIN_BACKUPS
from blocksync.net.node import Node
from blocksync.ui.strings import tr
from blocksync.ui.theme import Colors
from blocksync.ui.widgets import Dialog, set_variant


class SettingsDialog(Dialog):
    def __init__(self, node: Node, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.node = node
        s = node.settings
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumWidth(720)

        self.name_edit = QLineEdit(s.device_name)
        self.keep_spin = QSpinBox()
        self.keep_spin.setRange(MIN_BACKUPS, MAX_BACKUPS)
        self.keep_spin.setValue(s.keep_backups)
        self.full_backup_box = QCheckBox(tr("settings.full_backup"))
        self.full_backup_box.setChecked(s.full_backup)
        self.backups_edit = QLineEdit(str(s.backups_dir) if s.backups_dir else "")
        self.backups_edit.setPlaceholderText(str(node.paths.default_backups_dir))
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {Colors.RED.name()};")

        form = QFormLayout()
        form.addRow(tr("settings.device_name"), self.name_edit)
        form.addRow(tr("settings.keep_backups"), self.keep_spin)
        form.addRow("", self.full_backup_box)
        full_hint = QLabel(tr("settings.full_backup_hint"))
        full_hint.setWordWrap(True)
        full_hint.setProperty("role", "hint")
        form.addRow("", full_hint)
        form.addRow(tr("settings.backups_dir"), self._with_browse(self.backups_edit))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText(tr("settings.save"))
        set_variant(save_button, "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addLayout(form)
        hint = QLabel(tr("settings.hint"))
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

    def _with_browse(self, edit: QLineEdit) -> QWidget:
        button = QPushButton(tr("settings.browse"))
        button.clicked.connect(lambda: self._browse(edit))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        row.addWidget(button)
        widget = QWidget()
        widget.setLayout(row)
        return widget

    def _browse(self, edit: QLineEdit) -> None:
        start = edit.text() or edit.placeholderText()
        folder = QFileDialog.getExistingDirectory(self, tr("settings.choose_folder"), start)
        if folder:
            edit.setText(folder)

    def validate(self) -> str | None:
        """Текст ошибки или None, если всё в порядке."""
        if not self.name_edit.text().strip():
            return tr("settings.error_name")
        backups_text = self.backups_edit.text().strip()
        if backups_text:
            backups = Path(backups_text).expanduser().resolve()
            # Бэкапы внутри папки миров игра приняла бы за лишние миры.
            folders = [i.saves_dir for i in self.node.instances()]
            if any(backups.is_relative_to(folder.resolve()) for folder in folders):
                return tr("settings.error_backups_inside_saves")
        return None

    def save(self) -> None:
        error = self.validate()
        if error is not None:
            self.error_label.setText(error)
            return
        backups_text = self.backups_edit.text().strip()
        self.node.update_settings(
            device_name=self.name_edit.text().strip(),
            saves_dir=self.node.settings.saves_dir,
            backups_dir=Path(backups_text).expanduser() if backups_text else None,
            keep_backups=self.keep_spin.value(),
            full_backup=self.full_backup_box.isChecked(),
        )
        self.accept()
