"""Сборка MineSync в обычную программу: MineSync.exe на Windows, MineSync.app на macOS.

Запуск из папки проекта (в своём .venv):
    Windows: .venv\\Scripts\\python packaging\\build.py
    macOS:   .venv/bin/python packaging/build.py

Что происходит:
1. Рисуется иконка программы (блок травы) и сохраняется в build/icon.png.
2. PyInstaller по описанию packaging/MineSync.spec собирает программу в папку dist/.
Собирать нужно на каждой системе отдельно: .exe получается только на Windows, .app — только на Mac.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_SIZE = 1024


def make_icon(path: Path) -> None:
    """Иконка: объёмный блок травы на тёмной скруглённой плитке (рисуется кодом, как в программе)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # окно не нужно
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPixmap

    from blocksync.ui.widgets import iso_block

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 — нужен для рисования
    picture = QPixmap(ICON_SIZE, ICON_SIZE)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    tile = QPainterPath()
    margin = ICON_SIZE * 0.09
    tile.addRoundedRect(QRectF(margin, margin, ICON_SIZE - 2 * margin, ICON_SIZE - 2 * margin),
                        ICON_SIZE * 0.2, ICON_SIZE * 0.2)
    painter.fillPath(tile, QColor("#121b26"))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    block = iso_block(int(ICON_SIZE * 0.6))
    painter.drawPixmap((ICON_SIZE - block.width()) // 2, (ICON_SIZE - block.height()) // 2, block)
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    picture.save(str(path), "PNG")


def main() -> int:
    # В консоли английской Windows по умолчанию кодировка без русских букв.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print("1/2 Рисую иконку…")
    make_icon(ROOT / "build" / "icon.png")
    print("2/2 Собираю программу (несколько минут)…")
    result = subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
        str(ROOT / "packaging" / "MineSync.spec"),
    ], cwd=ROOT)
    if result.returncode != 0:
        print("Сборка не удалась — смотрите сообщения выше.")
        return result.returncode
    target = ROOT / "dist" / ("MineSync.app" if sys.platform == "darwin" else "MineSync")
    print(f"Готово: {target}")
    if sys.platform == "win32":
        print("Запускайте dist\\MineSync\\MineSync.exe. Переносить можно только всю папку dist\\MineSync целиком.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
