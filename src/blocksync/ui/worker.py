"""Фоновые задачи: долгие дела (сеть, сравнение, копирование) выполняются
вне главного потока, чтобы окно не зависало.

Как это работает: функция запускается в потоке из общего пула потоков Qt
(QThreadPool), а результат, ошибка и прогресс возвращаются в главный поток
через сигналы Qt. Сигнал, отправленный из другого потока, Qt ставит в очередь
главного потока, поэтому обработчики спокойно могут менять виджеты.

    run_task(lambda task: долгая_работа(), on_done=показать_результат, on_error=показать_ошибку)
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

logger = logging.getLogger(__name__)


class TaskSignals(QObject):
    finished = Signal(object)  # результат функции
    failed = Signal(object)  # исключение
    progress = Signal(object, object)  # (готово, всего); object, потому что int в Qt 32-битный


class Task(QRunnable):
    """Одна фоновая задача. Функция получает саму задачу: для прогресса и проверки отмены."""

    def __init__(self, function: Callable[["Task"], Any]) -> None:
        super().__init__()
        self.setAutoDelete(False)  # временем жизни управляет Python (см. _active)
        self.function = function
        self.signals = TaskSignals()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def report_progress(self, done: int, total: int) -> None:
        self.signals.progress.emit(done, total)

    def run(self) -> None:
        try:
            result = self.function(self)
        except BaseException as error:  # noqa: BLE001 — любую ошибку передаём в интерфейс
            self.signals.failed.emit(error)
        else:
            self.signals.finished.emit(result)


# Запущенные задачи. Без этой ссылки Python мог бы удалить задачу раньше,
# чем её сигналы дойдут до главного потока.
_active: set[Task] = set()


def run_task(
    function: Callable[[Task], Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Task:
    """Запустить функцию в фоне. Обработчики вызываются в главном потоке."""
    task = Task(function)
    if on_done is not None:
        task.signals.finished.connect(on_done)
    if on_error is not None:
        task.signals.failed.connect(on_error)
    if on_progress is not None:
        task.signals.progress.connect(on_progress)
    # Подключаем после обработчиков, чтобы задача удалялась последней.
    task.signals.finished.connect(lambda _: _active.discard(task))
    task.signals.failed.connect(lambda _: _active.discard(task))
    _active.add(task)
    QThreadPool.globalInstance().start(task)
    return task


def cancel_all_tasks(timeout_ms: int = 10_000) -> None:
    """При выходе из программы: попросить задачи остановиться и дождаться их."""
    for task in list(_active):
        task.cancel()
    if not QThreadPool.globalInstance().waitForDone(timeout_ms):
        logger.warning("Фоновые задачи не завершились за %s мс", timeout_ms)


class NodeBridge(QObject):
    """Передаёт события узла (они приходят из фоновых потоков) в главный поток."""

    changed = Signal()  # нашлось/пропало устройство, изменилась пара
    sync_requested = Signal(object)  # второе устройство просит забрать мир (protocol.SyncRequest)
