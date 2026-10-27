"""Окно синхронизации: «где играл последним?» -> сводка и предупреждения -> прогресс -> итог.

На первом шаге программа сама подсказывает ответ: сравнивает мир с тем, каким он
был после прошлой синхронизации (net/sync.suggest_direction), и отмечает вариант.

Если синхронизацию начали на другом устройстве (там выбрали «играл здесь»),
окно открывается сразу на шаге прогресса: этот компьютер забирает мир.
"""

import html
import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from blocksync.config.history import Direction as HistoryDirection
from blocksync.config.history import add_record, new_record
from blocksync.core.apply import SyncResult
from blocksync.core.checks import MASS_DELETE_THRESHOLD, SyncBlocker, SyncWarning
from blocksync.core.manifest import build_manifest
from blocksync.core.staging import staging_root
from blocksync.net.client import NetworkError, PeerClient
from blocksync.net.node import Node
from blocksync.net.pull import pull_world
from blocksync.net.sync import (
    Basis,
    Direction,
    Suggestion,
    SyncPreview,
    last_sync_state,
    local_side,
    prepare_sync,
    remote_side,
    suggest_direction,
)
from blocksync.ui.errors import describe_error
from blocksync.ui.format import format_count, format_duration, format_size
from blocksync.ui.strings import tr
from blocksync.ui.theme import Colors
from blocksync.ui.widgets import Dialog, Heading, PixelProgressBar, set_variant
from blocksync.ui.worker import Task, run_task

logger = logging.getLogger(__name__)

WARNING_KEYS = {
    SyncWarning.BOTH_CHANGED: "sync.warning_both_changed",
    SyncWarning.RECEIVER_CHANGED: "sync.warning_receiver_changed",
    SyncWarning.RECEIVER_NEWER: "sync.warning_receiver_newer",
    SyncWarning.DOWNGRADE: "sync.warning_downgrade",
    SyncWarning.MASS_DELETE: "sync.warning_mass_delete",
}
BLOCKER_KEYS = {
    SyncBlocker.WORLD_LOCKED: "sync.blocker_locked",
    SyncBlocker.NOT_ENOUGH_SPACE: "sync.blocker_space",
    SyncBlocker.SOURCE_MISSING: "sync.blocker_source_missing",
}
PROGRESS_STEPS = 1000  # полоса прогресса считает в тысячных долях
# Сообщать о сжатии, только если по сети ушло меньше 90% объёма файлов.
COMPRESSION_NOTICE_RATIO = 0.9


class SyncDialog(Dialog):
    # Мир успешно перенесён на этот компьютер: (название мира, откуда).
    synced = Signal(str, str)

    def __init__(
        self, node: Node, world: str, display_name: str, saves_dir: Path, remote_instance: str | None,
        local_instance: str | None = None, parent: QWidget | None = None, incoming: bool = False,
    ) -> None:
        """saves_dir — папка миров нашей сборки; remote_instance — связанная сборка на втором устройстве;
        local_instance — ID нашей сборки (его получит второе устройство при «играл здесь»)."""
        super().__init__(parent)
        self.node = node
        self.world = world
        self.saves_dir = saves_dir
        self.remote_instance = remote_instance
        self.local_instance = local_instance
        self.display_name = display_name
        self.peer_name = node.paired.name if node.paired else "?"
        self.direction: Direction | None = None
        self.preview: SyncPreview | None = None
        self.task: Task | None = None
        self._preview_token: object | None = None

        self.setWindowTitle(tr("sync.title", world=display_name))
        self.setMinimumWidth(620)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_choose_page())
        self.pages.addWidget(self._build_preview_page())
        self.pages.addWidget(self._build_progress_page())
        self.pages.addWidget(self._build_result_page())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(self.pages)

        if incoming:
            self.direction = Direction.PULL
            self.progress_title.setText(tr("sync.incoming", peer=self.peer_name, world=display_name))
            self._start_pull()
        else:
            self._suggest()

    # --- Шаг 1: где играл последним ---

    def _build_choose_page(self) -> QWidget:
        page = QWidget()
        question = Heading(tr("sync.where_played", world=self.display_name), 18)
        question.setWordWrap(True)
        self.here_radio = QRadioButton(tr("sync.this_computer", name=self.node.settings.device_name))
        self.peer_radio = QRadioButton(self.peer_name)
        self.suggestion_label = QLabel(tr("sync.suggest_checking"))
        self.suggestion_label.setWordWrap(True)
        self.suggestion_label.setProperty("role", "hint")
        # Сразу ничего не выбрано: вариант отметит подсказка, а если её нет — ответит пользователь.
        self.choice = QButtonGroup(self)
        self.choice.addButton(self.here_radio)
        self.choice.addButton(self.peer_radio)
        self.next_button = set_variant(QPushButton(tr("sync.next")), "primary")
        self.next_button.setEnabled(False)
        self.choice.buttonToggled.connect(lambda *_: self.next_button.setEnabled(True))
        self.next_button.clicked.connect(self._go_to_preview)
        cancel = QPushButton(tr("common.cancel"))
        cancel.clicked.connect(self.reject)

        layout = QVBoxLayout(page)
        layout.addWidget(question)
        layout.addWidget(self.here_radio)
        layout.addWidget(self.peer_radio)
        layout.addWidget(self.suggestion_label)
        layout.addStretch()
        layout.addLayout(_buttons(cancel, self.next_button))
        return page

    def _suggest(self) -> None:
        """В фоне сравнить мир с прошлой синхронизацией и подсказать, где играли последним."""
        node, world, saves_dir = self.node, self.world, self.saves_dir
        remote, local = self.remote_instance, self.local_instance

        def work(task: Task) -> Suggestion:
            with _peer_client(node, remote) as client:
                last_sync = last_sync_state(client, world, node.sync_states.load(local, world))
                there = remote_side(client, world).manifest
            return suggest_direction(local_side(saves_dir / world).manifest, there, last_sync)

        run_task(work, on_done=self._show_suggestion, on_error=lambda error: self.suggestion_label.hide())

    def _show_suggestion(self, suggestion: Suggestion) -> None:
        when = suggestion.last_sync.time.strftime("%d.%m %H:%M") if suggestion.last_sync else ""
        here_name = self.node.settings.device_name
        if suggestion.conflict:
            self.suggestion_label.setText(tr("sync.suggest_conflict", time=when))
            self.suggestion_label.setStyleSheet(f"color: {Colors.YELLOW.name()};")
            return
        if suggestion.direction is None:
            self.suggestion_label.hide()
            return
        here = suggestion.direction is Direction.PUSH
        keys = {
            Basis.LAST_SYNC: ("sync.suggest_here", "sync.suggest_there"),
            Basis.NEWER_FILES: ("sync.suggest_newer_here", "sync.suggest_newer_there"),
            None: ("sync.suggest_only_here", "sync.suggest_only_there"),
        }[suggestion.basis]
        self.suggestion_label.setText(tr(keys[0] if here else keys[1], time=when, here=here_name, peer=self.peer_name))
        # Не перебиваем выбор, если пользователь успел ответить сам.
        if self.choice.checkedButton() is None:
            (self.here_radio if here else self.peer_radio).setChecked(True)

    def _go_to_preview(self) -> None:
        self.direction = Direction.PULL if self.peer_radio.isChecked() else Direction.PUSH
        self.pages.setCurrentIndex(1)
        self.preview_direction.setText(self._direction_text())
        self.summary_label.setText(tr("sync.comparing"))
        self.warnings_label.hide()
        self.confirm_box.hide()
        self.confirm_box.setChecked(False)
        self.start_button.setEnabled(False)
        self.start_button.show()
        node, world, direction = self.node, self.world, self.direction
        saves_dir, remote, local = self.saves_dir, self.remote_instance, self.local_instance

        def work(task: Task) -> SyncPreview:
            with _peer_client(node, remote) as client:
                last_sync = last_sync_state(client, world, node.sync_states.load(local, world))
                preview = prepare_sync(client, world, direction, saves_dir, staging_root(saves_dir),
                                       node.settings.full_backup, last_sync)
            if preview.here and not preview.plan.has_changes and not preview.check.blockers:
                # Миры и так одинаковы: запоминаем это как состояние после синхронизации.
                node.sync_states.save(local, world, preview.here)
            return preview

        # Если пользователь успел вернуться назад и выбрать заново, ответ старого
        # сравнения нужно выбросить: показываем только результат последнего.
        token = object()
        self._preview_token = token
        self.task = run_task(
            work,
            on_done=lambda preview: self._show_preview(preview) if self._preview_token is token else None,
            on_error=lambda error: self._preview_failed(error) if self._preview_token is token else None,
        )

    def _direction_text(self) -> str:
        here = self.node.settings.device_name
        if self.direction is Direction.PULL:
            return tr("sync.direction", source=self.peer_name, target=here)
        return tr("sync.direction", source=here, target=self.peer_name)

    # --- Шаг 2: сводка, предупреждения, подтверждение ---

    def _build_preview_page(self) -> QWidget:
        page = QWidget()
        self.preview_direction = Heading("", 18, Colors.GREEN)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.warnings_label = QLabel()
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setTextFormat(Qt.TextFormat.RichText)
        self.confirm_box = QCheckBox(tr("sync.confirm_warnings"))
        self.confirm_box.toggled.connect(self._update_start_button)
        back = QPushButton(tr("sync.back"))
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.start_button = set_variant(QPushButton(tr("sync.start")), "primary")
        self.start_button.clicked.connect(self._start)

        layout = QVBoxLayout(page)
        layout.addWidget(self.preview_direction)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.warnings_label)
        layout.addWidget(self.confirm_box)
        layout.addStretch()
        layout.addLayout(_buttons(back, self.start_button))
        return page

    def _show_preview(self, preview: SyncPreview) -> None:
        self.task = None
        self.preview = preview
        plan, check = preview.plan, preview.check
        if not plan.has_changes and check.can_sync:
            self.summary_label.setText(tr("sync.nothing_to_do"))
            self.start_button.hide()
            return
        self.summary_label.setText(tr(
            "sync.summary",
            copy=format_count(len(plan.to_copy), "word.files"),
            size=format_size(plan.bytes_to_copy),
            delete=format_count(len(plan.to_delete), "word.files"),
        ))
        here_is_receiver = preview.direction is Direction.PULL
        receiver = self.node.settings.device_name if here_is_receiver else self.peer_name
        source = self.peer_name if here_is_receiver else self.node.settings.device_name
        values = {
            "receiver": receiver, "source": source, "percent": round(MASS_DELETE_THRESHOLD * 100),
            "required": format_size(check.required_bytes), "free": format_size(check.free_bytes),
        }
        lines = [_colored(tr("sync.blocker_prefix"), Colors.RED, tr(BLOCKER_KEYS[b], **values)) for b in check.blockers]
        lines += [_colored(tr("sync.warning_prefix"), Colors.YELLOW, tr(WARNING_KEYS[w], **values)) for w in check.warnings]
        self.warnings_label.setText("<br><br>".join(lines))
        self.warnings_label.setVisible(bool(lines))
        self.confirm_box.setVisible(bool(check.warnings) and check.can_sync)
        self._update_start_button()
        self._fit_to_text()

    def _preview_failed(self, error: BaseException) -> None:
        self.task = None
        self.summary_label.setText(describe_error(error))
        self.start_button.hide()

    def _update_start_button(self) -> None:
        preview = self.preview
        if preview is None:
            self.start_button.setEnabled(False)
            return
        confirmed = not preview.check.warnings or self.confirm_box.isChecked()
        self.start_button.setEnabled(preview.check.can_sync and confirmed)

    # --- Шаг 3: передача ---

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        self.progress_title = Heading("", 18)
        self.progress_title.setWordWrap(True)
        self.progress_bar = PixelProgressBar()
        self.progress_bar.setRange(0, PROGRESS_STEPS)
        self.progress_label = QLabel()
        self.cancel_button = set_variant(QPushButton(tr("common.cancel")), "danger")
        self.cancel_button.clicked.connect(self.cancel)

        layout = QVBoxLayout(page)
        layout.addWidget(self.progress_title)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)
        layout.addStretch()
        layout.addLayout(_buttons(self.cancel_button))
        return page

    def _start(self) -> None:
        if self.direction is Direction.PULL:
            self.progress_title.setText(self._direction_text())
            self._start_pull()
        else:
            self._request_push()

    def _start_pull(self) -> None:
        self.pages.setCurrentIndex(2)
        self.progress_label.setText(tr("sync.preparing"))
        node, world = self.node, self.world
        display_name, peer = self.display_name, self.peer_name
        saves_dir, remote, local = self.saves_dir, self.remote_instance, self.local_instance

        def work(task: Task) -> SyncResult:
            with _peer_client(node, remote) as client:
                result = pull_world(
                    client, world, saves_dir, staging_root(saves_dir), node.backups_root_for(local),
                    node.settings.keep_backups, task.report_progress, task.is_cancelled,
                    full_backup=node.settings.full_backup,
                )
            # Теперь мир здесь такой же, как там: запоминаем, каким он стал (для подсказки в следующий раз).
            node.sync_states.save(local, world, build_manifest(saves_dir / world))
            if result.copied_files or result.deleted_files:
                add_record(node.paths.history_file, new_record(
                    display_name, HistoryDirection.RECEIVED, peer,
                    copied_files=result.copied_files, deleted_files=result.deleted_files,
                    bytes_copied=result.bytes_copied, duration_seconds=round(result.duration_seconds, 1),
                    folder=world, instance=local or "",
                ))
            return result

        self.task = run_task(work, on_done=self._pull_done, on_error=self._failed, on_progress=self._progress)

    def _progress(self, done: int, total: int) -> None:
        if total:
            self.progress_bar.setValue(int(done * PROGRESS_STEPS / total))
        self.progress_label.setText(tr("sync.progress", done=format_size(done), total=format_size(total)))

    def _request_push(self) -> None:
        """Эталон — этот компьютер: просим другое устройство забрать мир, работу делает оно."""
        self.pages.setCurrentIndex(2)
        self.progress_title.setText(self._direction_text())
        self.progress_bar.setRange(0, 0)  # «бегущая» полоса: сколько ждать, неизвестно
        self.progress_label.setText(tr("sync.sending_request"))
        node, world = self.node, self.world
        display_name, peer = self.display_name, self.peer_name
        remote, local = self.remote_instance, self.local_instance

        def work(task: Task) -> None:
            with _peer_client(node, remote) as client:
                client.request_sync(world, our_instance=local)
            add_record(node.paths.history_file, new_record(
                display_name, HistoryDirection.SENT, peer, folder=world, instance=local or "",
            ))

        self.task = run_task(work, on_done=lambda _: self._push_requested(), on_error=self._failed)

    # --- Шаг 4: итог ---

    def _build_result_page(self) -> QWidget:
        page = QWidget()
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        close = set_variant(QPushButton(tr("common.close")), "primary")
        close.clicked.connect(self.accept)

        layout = QVBoxLayout(page)
        layout.addWidget(self.result_label)
        layout.addStretch()
        layout.addLayout(_buttons(close))
        return page

    def _pull_done(self, result: SyncResult) -> None:
        self.task = None
        text = tr(
            "sync.done", duration=format_duration(result.duration_seconds),
            copied=format_count(result.copied_files, "word.files"), size=format_size(result.bytes_copied),
            deleted=format_count(result.deleted_files, "word.files"),
        )
        sent = result.bytes_transferred
        if sent is not None and sent < result.bytes_copied * COMPRESSION_NOTICE_RATIO:
            saved = round((1 - sent / result.bytes_copied) * 100)
            text += "\n" + tr("sync.compressed", sent=format_size(sent), percent=saved)
        if result.backup_path is not None:
            text += "\n\n" + tr("sync.backup_saved", path=result.backup_path)
        elif result.copied_files or result.deleted_files:
            text += "\n\n" + tr("sync.no_backup_needed")
        self._show_result(text)
        if result.copied_files or result.deleted_files:
            self.synced.emit(self.display_name, self.peer_name)

    def _push_requested(self) -> None:
        self.task = None
        self._show_result(tr("sync.push_requested", peer=self.peer_name, world=self.display_name))

    def _failed(self, error: BaseException) -> None:
        self.task = None
        self._show_result(describe_error(error))

    def _show_result(self, text: str) -> None:
        self.result_label.setText(text)
        self.pages.setCurrentIndex(3)
        self._fit_to_text()

    def _fit_to_text(self) -> None:
        """Окно подрастает под длинный текст (переносы строк Qt сам по высоте не учитывает)."""
        page = self.pages.currentWidget()
        page_layout, own_layout = page.layout() if page else None, self.layout()
        if page is None or page_layout is None or own_layout is None:
            return
        page_layout.activate()
        needed = page_layout.totalHeightForWidth(page.width()) if page_layout.hasHeightForWidth() else 0
        margins = own_layout.contentsMargins()
        self.resize(self.width(), max(self.height(), needed + margins.top() + margins.bottom()))

    # --- Отмена и закрытие ---

    def cancel(self) -> None:
        """Отменить передачу. Мир не меняется: временная папка удаляется (core/apply.py)."""
        if self.task is not None:
            self.task.cancel()
            self.cancel_button.setEnabled(False)
            self.progress_label.setText(tr("sync.cancelling"))

    def reject(self) -> None:
        # Esc или крестик во время передачи: сначала отменяем и ждём, пока задача остановится.
        if self.task is not None and self.pages.currentIndex() == 2:
            self.cancel()
            return
        if self.task is not None:
            self.task.cancel()
        super().reject()


def _colored(prefix: str, color: QColor, text: str) -> str:
    """Строка предупреждения: цветное слово-приставка и текст (в формате HTML)."""
    return f'<span style="color: {color.name()}">{html.escape(prefix)}</span> {html.escape(text)}'


def _buttons(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch()
    for button in buttons:
        row.addWidget(button)
    return row


def _peer_client(node: Node, instance: str | None) -> PeerClient:
    client = node.peer_client(instance)
    if client is None:
        raise NetworkError("другое устройство не в сети", "net_offline")
    return client
