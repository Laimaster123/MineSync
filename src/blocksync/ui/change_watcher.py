"""Слежение за мирами без нагрузки на диск.

- Папки миров «слушает» QFileSystemWatcher: система сама сообщает, когда в папке
  что-то изменилось (например, игра сохранила level.dat). Это не опрос диска.
  Игра при сохранении пишет много файлов подряд, поэтому сигнал changed
  подаётся один раз — через SETTLE_MS после последнего изменения.
- Открыт ли мир в игре, видно только по блокировке session.lock, а она папку
  не меняет. Поэтому раз в LOCK_POLL_MS в фоне проверяются только эти маленькие файлы.
"""

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from blocksync.core.lock import is_world_locked
from blocksync.ui.worker import Task, run_task

SETTLE_MS = 3000
LOCK_POLL_MS = 5000


class WorldWatcher(QObject):
    changed = Signal()  # что-то в мирах изменилось — пора обновить список

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self.changed.emit)
        self._lock_timer = QTimer(self)
        self._lock_timer.setInterval(LOCK_POLL_MS)
        self._lock_timer.timeout.connect(self._poll_locks)
        self._locks: dict[Path, bool] = {}
        self._lock_task: Task | None = None
        self.paused = False  # во время синхронизации не дёргаем список: он обновится после неё

    def watch(self, folders: list[Path], locks: dict[Path, bool]) -> None:
        """Следить за папками folders; locks — папки миров и открыты ли они сейчас в игре."""
        wanted = {str(folder) for folder in folders if folder.is_dir()}
        current = set(self._watcher.directories())
        if current - wanted:
            self._watcher.removePaths(sorted(current - wanted))
        if wanted - current:
            self._watcher.addPaths(sorted(wanted - current))
        self._locks = dict(locks)
        if not self._lock_timer.isActive():
            self._lock_timer.start()

    def watched(self) -> list[str]:
        return self._watcher.directories()

    def _on_directory_changed(self, path: str) -> None:
        if not self.paused:
            self._settle.start()  # перезапуск: ждём, пока изменения затихнут

    def _poll_locks(self) -> None:
        if self.paused or self._lock_task is not None or not self._locks:
            return
        known = dict(self._locks)

        def work(task: Task) -> bool:
            return any(is_world_locked(world) != locked for world, locked in known.items() if world.is_dir())

        def done(changed: bool) -> None:
            self._lock_task = None
            if changed:
                self.changed.emit()

        def failed(error: BaseException) -> None:
            self._lock_task = None

        self._lock_task = run_task(work, on_done=done, on_error=failed)
