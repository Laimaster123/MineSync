"""Тема оформления: современная тёмная, с пиксельными акцентами в духе Майнкрафта.

Из чего она состоит:
- шрифты: Inter — весь текст, Press Start 2P — пиксельный логотип и код сопряжения
  (оба бесплатные, лицензия OFL, лежат в resources/fonts);
- стандартный стиль Qt «Fusion»: одинаково выглядит на Windows и macOS;
- палитра и таблица стилей (QSS, похожа на CSS из веба): цвета, скругления, отступы.
"""

import logging

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from blocksync.resources import resource_path

logger = logging.getLogger(__name__)

FONT_FILES = ["fonts/Inter-Regular.ttf", "fonts/Inter-SemiBold.ttf", "fonts/PressStart2P-Regular.ttf"]
TEXT_FAMILY = "Inter"
PIXEL_FAMILY = "Press Start 2P"
TEXT_SIZE = 14  # обычный текст, пикселей


class Colors:
    """Цвета темы в одном месте."""

    WINDOW = QColor("#0d141c")
    PANEL = QColor("#121b26")
    PANEL_BORDER = QColor("#223042")
    ROW_HOVER = QColor("#172333")
    FIELD = QColor("#0a1017")
    TEXT = QColor("#e6edf3")
    TEXT_DIM = QColor("#8b98a9")
    TEXT_SHADOW = QColor("#05080c")
    ACCENT = QColor("#e3b341")
    GREEN = QColor("#3fb950")
    YELLOW = QColor("#e3b341")
    BLUE = QColor("#58a6ff")
    RED = QColor("#f85149")
    GRAY = QColor("#6e7b8b")


def load_fonts() -> None:
    """Подключить шрифты из ресурсов программы."""
    for file in FONT_FILES:
        if QFontDatabase.addApplicationFont(str(resource_path(file))) < 0:
            logger.error("Не удалось загрузить шрифт %s", file)


def text_font(size: int = TEXT_SIZE, bold: bool = False) -> QFont:
    font = QFont(TEXT_FAMILY)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def pixel_font(size: int) -> QFont:
    """Пиксельный шрифт без сглаживания: буквы остаются «квадратными»."""
    font = QFont(PIXEL_FAMILY)
    font.setPixelSize(size)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    return font


def rgba(color: QColor, alpha: float) -> str:
    """Цвет для QSS с прозрачностью alpha от 0 до 1."""
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {round(alpha * 255)})"


def palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: Colors.WINDOW,
        QPalette.ColorRole.WindowText: Colors.TEXT,
        QPalette.ColorRole.Base: Colors.FIELD,
        QPalette.ColorRole.AlternateBase: Colors.PANEL,
        QPalette.ColorRole.Text: Colors.TEXT,
        QPalette.ColorRole.Button: Colors.PANEL,
        QPalette.ColorRole.ButtonText: Colors.TEXT,
        QPalette.ColorRole.Highlight: QColor("#238636"),
        QPalette.ColorRole.HighlightedText: Colors.TEXT,
        QPalette.ColorRole.ToolTipBase: Colors.PANEL,
        QPalette.ColorRole.ToolTipText: Colors.TEXT,
        QPalette.ColorRole.PlaceholderText: Colors.TEXT_DIM,
        QPalette.ColorRole.Link: Colors.GREEN,
    }
    for role, color in roles.items():
        p.setColor(role, color)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, Colors.GRAY)
    return p


def style_sheet() -> str:
    c = Colors
    return f"""
    QWidget {{ color: {c.TEXT.name()}; }}
    QDialog, QMessageBox {{ background: {c.WINDOW.name()}; }}
    QToolTip {{
        color: {c.TEXT.name()}; background: {c.PANEL.name()};
        border: 1px solid {c.PANEL_BORDER.name()}; padding: 6px; border-radius: 6px;
    }}

    /* Кнопки: обычная, зелёная главная (variant="primary") и красная (variant="danger"). */
    QPushButton {{
        background: #1a2533; border: 1px solid #2c3b4f; border-radius: 8px;
        padding: 9px 16px; font-size: 14px; font-weight: 600;
    }}
    QPushButton:hover {{ background: #213044; border-color: #3a4d66; }}
    QPushButton:pressed {{ background: #162130; padding-top: 10px; padding-bottom: 8px; }}
    QPushButton:disabled {{ color: {c.GRAY.name()}; background: #151e29; border-color: #1f2a38; }}
    QPushButton[variant="primary"] {{
        color: white; border: 1px solid #46c35a;
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3fb950, stop:1 #2a9142);
    }}
    QPushButton[variant="primary"]:hover {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4cca5e, stop:1 #31a34b);
    }}
    QPushButton[variant="primary"]:pressed {{ background: #26843c; }}
    QPushButton[variant="primary"]:disabled {{
        color: {c.GRAY.name()}; background: #151e29; border-color: #1f2a38;
    }}
    QPushButton[variant="danger"] {{ color: #ff7b72; background: #2a1519; border-color: #6e2a2f; }}
    QPushButton[variant="danger"]:hover {{ background: #3a1b20; border-color: {c.RED.name()}; }}
    QPushButton[variant="icon"] {{ padding: 6px; min-width: 22px; }}
    QPushButton[variant="tab"] {{
        background: transparent; border: 1px solid transparent; color: {c.TEXT_DIM.name()};
        padding: 7px 14px;
    }}
    QPushButton[variant="tab"]:hover {{ background: {c.ROW_HOVER.name()}; color: {c.TEXT.name()}; }}
    QPushButton[variant="tab"]:checked {{
        background: {rgba(c.GREEN, 0.14)}; border-color: {rgba(c.GREEN, 0.6)}; color: {c.TEXT.name()};
    }}
    QPushButton[buttonSize="big"] {{ font-size: 18px; padding: 14px 24px; border-radius: 10px; }}

    /* Панели (Panel в widgets.py). */
    QFrame[panel="true"] {{
        background: {c.PANEL.name()}; border: 1px solid {c.PANEL_BORDER.name()}; border-radius: 12px;
    }}
    QFrame[panel="true"] QLabel {{ background: transparent; border: none; }}

    QLineEdit, QSpinBox, QComboBox {{
        background: {c.FIELD.name()}; border: 1px solid {c.PANEL_BORDER.name()}; border-radius: 8px;
        padding: 7px 10px; selection-background-color: #238636;
    }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {c.GREEN.name()}; }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox QAbstractItemView {{
        background: {c.PANEL.name()}; border: 1px solid {c.PANEL_BORDER.name()};
        selection-background-color: #238636; outline: none;
    }}

    QCheckBox, QRadioButton {{ spacing: 10px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 18px; height: 18px; background: {c.FIELD.name()}; border: 1px solid #3a4d66;
    }}
    QCheckBox::indicator {{ border-radius: 5px; }}
    QRadioButton::indicator {{ border-radius: 10px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {c.GREEN.name()}; }}
    QCheckBox::indicator:checked {{ background: {c.GREEN.name()}; border-color: {c.GREEN.name()}; }}
    QRadioButton::indicator:checked {{
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
            stop:0 {c.GREEN.name()}, stop:0.45 {c.GREEN.name()}, stop:0.55 {c.FIELD.name()});
        border-color: {c.GREEN.name()};
    }}

    QTreeWidget {{ background: transparent; border: none; outline: none; }}
    QTreeWidget::item {{ padding: 6px 2px; }}
    QTreeWidget::item:hover {{ background: {c.ROW_HOVER.name()}; }}
    QTreeWidget::item:selected {{ background: {rgba(c.GREEN, 0.18)}; color: {c.TEXT.name()}; }}
    QHeaderView {{ background: transparent; border: none; }}
    QHeaderView::section {{
        background: transparent; color: {c.TEXT_DIM.name()}; border: none;
        padding: 4px; min-height: 26px;
    }}

    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: #2c3b4f; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: #3a4d66; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

    QGroupBox {{
        border: 1px solid {c.PANEL_BORDER.name()}; border-radius: 12px; background: {c.PANEL.name()};
        margin-top: 22px; padding: 16px 12px 12px 12px; font-weight: 600;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {c.TEXT_DIM.name()}; }}
    QStatusBar {{ color: {c.ACCENT.name()}; }}
    QLabel[role="hint"] {{ color: {c.TEXT_DIM.name()}; }}
    """


def apply_theme(app: QApplication) -> None:
    """Включить тему для всего приложения."""
    load_fonts()
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setFont(text_font())
    app.setStyleSheet(style_sheet())
