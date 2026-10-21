"""Окно сопряжения: показать код на этом компьютере или ввести код с другого, забыть устройство."""

import time

from PySide6.QtCore import QRegularExpression, Qt, QTimer
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from blocksync.config.settings import PairedDevice
from blocksync.net.node import Node
from blocksync.net.pairing import CODE_LIFETIME_SECONDS
from blocksync.ui.errors import describe_error
from blocksync.ui.strings import tr
from blocksync.ui.theme import Colors
from blocksync.ui.widgets import Dialog, PixelText, set_variant
from blocksync.ui.worker import NodeBridge, Task, run_task


class PairingDialog(Dialog):
    def __init__(self, node: Node, bridge: NodeBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.node = node
        self.bridge = bridge
        self.task: Task | None = None
        self._code_deadline = 0.0
        self.setWindowTitle(tr("pairing.title"))
        self.setMinimumWidth(620)

        # Текущая пара.
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.forget_button = set_variant(QPushButton(tr("pairing.forget")), "danger")
        self.forget_button.clicked.connect(self.forget)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.forget_button)

        # Показать код.
        show_box = QGroupBox(tr("pairing.show_code_group"))
        self.show_code_button = QPushButton(tr("pairing.show_code"))
        self.show_code_button.clicked.connect(self.show_code)
        self.code_label = PixelText("", 32, Colors.ACCENT)
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_hint = QLabel()
        self.code_hint.setWordWrap(True)
        show_layout = QVBoxLayout(show_box)
        show_layout.addWidget(QLabel(tr("pairing.show_code_hint")))
        show_layout.addWidget(self.show_code_button)
        show_layout.addWidget(self.code_label)
        show_layout.addWidget(self.code_hint)

        # Ввести код.
        enter_box = QGroupBox(tr("pairing.enter_code_group"))
        self.device_combo = QComboBox()
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("123456")
        self.code_edit.setMaxLength(6)
        self.code_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,6}")))
        self.code_edit.textChanged.connect(self._update_pair_button)
        self.pair_button = set_variant(QPushButton(tr("pairing.pair")), "primary")
        self.pair_button.clicked.connect(self.pair)
        self.enter_result = QLabel()
        self.enter_result.setWordWrap(True)
        enter_row = QHBoxLayout()
        enter_row.addWidget(self.device_combo, 1)
        enter_row.addWidget(self.code_edit)
        enter_row.addWidget(self.pair_button)
        enter_layout = QVBoxLayout(enter_box)
        enter_layout.addWidget(QLabel(tr("pairing.enter_code_hint")))
        enter_layout.addLayout(enter_row)
        enter_layout.addWidget(self.enter_result)

        close = QPushButton(tr("common.close"))
        close.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addLayout(status_row)
        layout.addWidget(show_box)
        layout.addWidget(enter_box)
        layout.addLayout(close_row)

        # Раз в секунду обновляем обратный отсчёт кода.
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        bridge.changed.connect(self._update)
        self._update()

    # --- Состояние ---

    def _update(self) -> None:
        paired = self.node.paired
        if paired is None:
            self.status_label.setText(tr("pairing.not_paired"))
        else:
            self.status_label.setText(tr("pairing.paired_with", name=paired.name))
        self.forget_button.setVisible(paired is not None)
        if paired is not None and self._timer.isActive():
            # Пока код был на экране, другое устройство его ввело.
            self._hide_code()
            self.code_hint.setText(tr("pairing.success", name=paired.name))

        current = self.device_combo.currentData()
        self.device_combo.clear()
        for device in self.node.discovery.devices():
            self.device_combo.addItem(device.name, device.device_id)
        if self.device_combo.count() == 0:
            self.device_combo.addItem(tr("pairing.no_devices"), None)
        index = self.device_combo.findData(current)
        if index >= 0:
            self.device_combo.setCurrentIndex(index)
        self._update_pair_button()

    def _update_pair_button(self) -> None:
        ready = len(self.code_edit.text()) == 6 and self.device_combo.currentData() is not None
        self.pair_button.setEnabled(ready and self.task is None)

    # --- Показать код ---

    def show_code(self) -> None:
        code = self.node.show_code()
        self.code_label.setText(f"{code[:3]} {code[3:]}")
        self._code_deadline = time.monotonic() + CODE_LIFETIME_SECONDS
        self._timer.start()
        self._tick()

    def _tick(self) -> None:
        left = round(self._code_deadline - time.monotonic())
        if left <= 0 or not self.node.pairing.is_active:
            self._hide_code()
            self.code_hint.setText(tr("pairing.code_expired"))
            return
        minutes, seconds = divmod(left, 60)
        self.code_hint.setText(tr("pairing.code_left", time=f"{minutes}:{seconds:02d}"))

    def _hide_code(self) -> None:
        self._timer.stop()
        self.code_label.setText("")

    # --- Ввести код ---

    def pair(self) -> None:
        device = self.node.discovery.find(self.device_combo.currentData() or "")
        if device is None:
            self.enter_result.setText(tr("pairing.device_gone"))
            return
        code = self.code_edit.text()
        node = self.node
        self.enter_result.setText(tr("pairing.pairing"))
        self.task = run_task(lambda task: node.pair(device, code), on_done=self._paired, on_error=self._failed)
        self._update_pair_button()

    def _paired(self, paired: PairedDevice) -> None:
        self.task = None
        self.code_edit.clear()
        self.enter_result.setText(tr("pairing.success", name=paired.name))
        self._update()

    def _failed(self, error: BaseException) -> None:
        self.task = None
        self.enter_result.setText(describe_error(error))
        self.forget_button.setEnabled(True)
        self._update()

    # --- Забыть ---

    def forget(self) -> None:
        node = self.node
        self.forget_button.setEnabled(False)

        def done(_: object) -> None:
            self.task = None
            self.forget_button.setEnabled(True)
            self._update()

        self.task = run_task(lambda task: node.forget(), on_done=done, on_error=self._failed)

    def done(self, result: int) -> None:
        # Окно закрыли — показанный код больше не нужен.
        if self._timer.isActive():
            self.node.pairing.cancel()
        self._timer.stop()
        self.bridge.changed.disconnect(self._update)
        super().done(result)
