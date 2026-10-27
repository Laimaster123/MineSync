"""Точка входа: python -m blocksync"""

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from blocksync.config.logs import setup_logging
from blocksync.config.paths import AppPaths
from blocksync.config.app_lock import AlreadyRunningError
from blocksync.net.node import Node
from blocksync.ui.main_window import MainWindow
from blocksync.ui.strings import tr
from blocksync.ui.theme import apply_theme
from blocksync.ui.worker import NodeBridge, cancel_all_tasks

logger = logging.getLogger("blocksync")


def main() -> int:
    """Создать приложение, запустить сеть, показать главное окно и запустить цикл событий Qt."""
    app = QApplication(sys.argv)
    app.setApplicationName("MineSync")
    apply_theme(app)

    paths = AppPaths.default()
    log_file = setup_logging(paths.logs_dir)
    logger.info("MineSync запускается, лог: %s", log_file)

    # События сети приходят из фоновых потоков; мост передаёт их в окно.
    bridge = NodeBridge()
    try:
        node = Node(paths, on_change=bridge.changed.emit, on_sync_request=bridge.sync_requested.emit)
        node.start()
    except AlreadyRunningError:
        QMessageBox.information(None, "MineSync", tr("error.already_running"))
        return 1
    except OSError as error:
        logger.exception("Не удалось запустить сеть")
        QMessageBox.critical(None, "MineSync", tr("error.start_failed", details=error))
        return 1

    window = MainWindow(node, bridge)
    window.show()
    if node.broken_settings is not None:
        QMessageBox.warning(window, "MineSync", tr("error.settings_broken", path=node.broken_settings))
    try:
        # exec() работает, пока окно не закрыто, и возвращает код завершения.
        return app.exec()
    finally:
        cancel_all_tasks()
        node.stop()
        logger.info("MineSync завершён")


if __name__ == "__main__":
    sys.exit(main())
