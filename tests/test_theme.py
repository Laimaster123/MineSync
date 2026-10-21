"""Тема: шрифты подключаются, значки и текстуры рисуются, виджеты отрисовываются без ошибок."""

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton
from pytestqt.qtbot import QtBot

from blocksync.ui import icons, pixel_art, theme
from blocksync.ui.widgets import PixelProgressBar, default_world_icon, iso_block, world_icon_from_png


def test_fonts_are_loaded(qapp: QApplication) -> None:
    theme.apply_theme(qapp)
    families = QFontDatabase.families()
    assert "Inter" in families and "Press Start 2P" in families
    assert QApplication.font().family() == "Inter"


def test_pixel_icons() -> None:
    picture = icons.pixmap("sync", "#ffffff", 24)
    assert picture.width() == 24 and not picture.toImage().isNull()
    assert all(len(row) == icons.GRID for name in icons.PATTERNS for row in icons._rows(name))
    assert all(len(icons._rows(name)) == icons.GRID for name in icons.PATTERNS)
    assert iso_block(48).width() == 48


def test_textures_are_stable() -> None:
    # Одно и то же зерно — один и тот же узор при каждом запуске.
    assert pixel_art.dirt(5) == pixel_art.dirt(5)
    assert pixel_art.stone(1) != pixel_art.stone(2)
    assert pixel_art.scaled("grass_side", 4).width() == 64


def test_widgets_paint(qapp: QApplication, qtbot: QtBot) -> None:
    theme.apply_theme(qapp)
    bar = PixelProgressBar()
    qtbot.addWidget(bar)
    bar.resize(400, 40)
    for maximum, value in ((1000, 0), (1000, 437), (1000, 1000), (0, 0)):
        bar.setRange(0, maximum)
        bar.setValue(value)
        assert not bar.grab().isNull()

    button = QPushButton("Синхронизировать")
    qtbot.addWidget(button)
    button.setProperty("variant", "primary")
    assert button.sizeHint().height() >= 32
    assert not button.grab().isNull()


def test_world_icons() -> None:
    assert not default_world_icon(32).isNull()
    assert world_icon_from_png(b"not a png", 32) is None
