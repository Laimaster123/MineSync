"""Виджеты темы: панель, заголовки, «таблетка» статуса, логотип, полоска прогресса, иконки миров."""

from functools import cache

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from blocksync.ui import icons, pixel_art
from blocksync.ui.theme import Colors, pixel_font, rgba, text_font


class Panel(QFrame):
    """Скруглённая тёмная панель (цвета и рамка — в theme.style_sheet)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("panel", True)


class Dialog(QDialog):
    """Диалог в цветах темы (фон задаётся таблицей стилей)."""


class Heading(QLabel):
    """Заголовок: полужирный текст покрупнее."""

    def __init__(self, text: str = "", size: int = 17, color: QColor = Colors.TEXT, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(text_font(size, bold=True))
        self.setStyleSheet(f"color: {color.name()};")


class PixelText(QLabel):
    """Надпись пиксельным шрифтом с жёсткой тенью — для логотипа и кода сопряжения."""

    def __init__(self, text: str, size: int, color: QColor = Colors.TEXT, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(pixel_font(size))
        self.setStyleSheet(f"color: {color.name()};")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(0)
        offset = max(2, size // 8)
        shadow.setOffset(offset, offset)
        shadow.setColor(Colors.TEXT_SHADOW)
        self.setGraphicsEffect(shadow)


def with_icon(button: QPushButton, icon_name: str, color: str, text: str) -> QPushButton:
    """Кнопка со значком слева. Пробелы в начале — небольшой отступ между значком и текстом."""
    button.setIcon(icons.icon(icon_name, color, 24))
    button.setText(f"  {text}")
    return button


def set_variant(button: QPushButton, variant: str | None) -> QPushButton:
    """Вид кнопки: "primary" — зелёная, "danger" — красная, "icon" — только значок, None — обычная."""
    button.setProperty("variant", variant)
    # Qt не перечитывает стиль сам, когда меняется свойство, — просим явно.
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def big_button(button: QPushButton) -> QPushButton:
    set_variant(button, "primary")
    button.setProperty("buttonSize", "big")
    return button


def icon_button(name: str, tooltip: str) -> QPushButton:
    """Маленькая кнопка с одним значком."""
    button = QPushButton()
    button.setIcon(icons.icon(name, Colors.TEXT_DIM.name(), 24))
    button.setToolTip(tooltip)
    return set_variant(button, "icon")


class StatusDot(QWidget):
    """Круглая «лампочка» статуса со свечением."""

    SIZE = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE + 8, self.SIZE + 8)
        self._color = Colors.GRAY

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QColor(self._color)
        glow.setAlpha(60)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(self.rect())
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect().adjusted(4, 4, -4, -4))


class StatusPill(QFrame):
    """Цветная «таблетка»: значок и короткий текст, например «✓ Одинаковы»."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_label = QLabel()
        self.text_label = QLabel()
        self.text_label.setFont(text_font(13, bold=True))
        self.setFixedHeight(30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 14, 0)
        layout.setSpacing(8)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)

    def set_status(self, text: str, icon_name: str, color: QColor) -> None:
        self.text_label.setText(text)
        self.text_label.setStyleSheet(f"color: {color.name()}; background: transparent; border: none;")
        self.icon_label.setPixmap(icons.pixmap(icon_name, color.name(), 12))
        self.icon_label.setStyleSheet("background: transparent; border: none;")
        self.setStyleSheet(
            f"StatusPill {{ background: {rgba(color, 0.12)}; border: 1px solid {rgba(color, 0.55)};"
            f" border-radius: 15px; }}"
        )


# --- Логотип и украшения из блоков ---


@cache
def iso_block(width: int, texture_seed: int = 4) -> QPixmap:
    """Объёмный блок травы (вид сверху-сбоку), нарисованный из своих текстур без сглаживания."""
    w = float(width)
    result = QPixmap(width, width)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    side = pixel_art.scaled("grass_side", 1, texture_seed)
    top = pixel_art.scaled("grass_top", 1, texture_seed + 1)
    square = QPolygonF([QPointF(0, 0), QPointF(16, 0), QPointF(16, 16), QPointF(0, 16)])
    faces = [
        (top, [(w / 2, 0), (w, w / 4), (w / 2, w / 2), (0, w / 4)], 0),
        (side, [(0, w / 4), (w / 2, w / 2), (w / 2, w), (0, w * 3 / 4)], 50),
        (side, [(w / 2, w / 2), (w, w / 4), (w, w * 3 / 4), (w / 2, w)], 100),
    ]
    for texture, points, shade in faces:
        target = QPolygonF([QPointF(x, y) for x, y in points])
        transform = QTransform()
        if QTransform.quadToQuad(square, target, transform):
            painter.setTransform(transform)
            painter.drawPixmap(0, 0, texture)
            if shade:
                painter.fillRect(QRect(0, 0, 16, 16), QColor(0, 0, 0, shade))
            painter.resetTransform()
    painter.end()
    return result


# Украшения в нижних углах окна: (x от края, y от низа, размер блока, прозрачность).
CORNER_BLOCKS = [(-6, 64, 64, 0.9), (44, 40, 44, 0.7), (6, 110, 32, 0.45), (92, 26, 28, 0.4)]
CORNER_PIXELS = [(70, 92, 10), (120, 64, 8), (30, 150, 8), (150, 40, 6), (100, 120, 6)]


def paint_corner_decorations(painter: QPainter, rect: QRect) -> None:
    """Блоки травы и «пиксели» в нижних углах, как на макете."""
    for mirror in (False, True):
        for x, y, size, opacity in CORNER_BLOCKS:
            left = rect.right() - x - size if mirror else rect.left() + x
            painter.setOpacity(opacity)
            painter.drawPixmap(left, rect.bottom() - y, iso_block(size))
        for x, y, size in CORNER_PIXELS:
            left = rect.right() - x - size if mirror else rect.left() + x
            painter.setOpacity(0.35)
            painter.fillRect(QRect(left, rect.bottom() - y, size, size), Colors.GREEN)
    painter.setOpacity(1.0)


class Background(QWidget):
    """Подложка главного окна: тёмный фон с лёгким градиентом и блоками по углам."""

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Colors.WINDOW)
        paint_corner_decorations(painter, self.rect())


class PixelProgressBar(QWidget):
    """Полоска прогресса из блоков травы; последний заполненный блок «мигает».

    Если максимум равен 0, сколько ждать — неизвестно: по полоске бегает один блок.
    Методы как у QProgressBar: setRange, setValue, value.
    """

    BLOCK = 22
    GAP = 3
    FRAME_MS = 80

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(self.BLOCK + 16)
        self._minimum, self._maximum, self._value = 0, 100, 0
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._next_frame)

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum, self._maximum = minimum, maximum
        self.update()

    def setValue(self, value: int) -> None:
        self._value = value
        self.update()

    def value(self) -> int:
        return self._value

    def showEvent(self, event: object) -> None:
        self._timer.start()

    def hideEvent(self, event: object) -> None:
        self._timer.stop()

    def _next_frame(self) -> None:
        self._frame += 1
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QPainterPath()
        frame.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        painter.fillPath(frame, Colors.FIELD)
        painter.setPen(Colors.PANEL_BORDER)
        painter.drawPath(frame)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        inner = self.rect().adjusted(8, 0, -8, 0)
        slots = max(1, (inner.width() + self.GAP) // (self.BLOCK + self.GAP))
        block = pixel_art.scaled("grass_side", 2, 7).scaled(
            self.BLOCK, self.BLOCK, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation
        )
        top = (self.height() - self.BLOCK) // 2

        def spot(index: int) -> QRect:
            return QRect(inner.left() + index * (self.BLOCK + self.GAP), top, self.BLOCK, self.BLOCK)

        span = self._maximum - self._minimum
        if span <= 0:
            cycle = max(1, 2 * (slots - 1))
            step = self._frame % cycle
            painter.drawPixmap(spot(step if step < slots else cycle - step), block)
            return

        fraction = min(1.0, max(0.0, (self._value - self._minimum) / span))
        filled = fraction * slots
        whole = int(filled)
        for index in range(whole):
            painter.drawPixmap(spot(index), block)
        if whole < slots:
            # Блок, который «строится» сейчас: заполнен частично и мерцает.
            part = filled - whole
            blink = self._frame % 10 < 5
            if part > 0 or blink:
                height = max(3, int(self.BLOCK * part))
                place = spot(whole)
                painter.drawPixmap(
                    QRect(place.x(), place.bottom() - height + 1, self.BLOCK, height), block,
                    QRect(0, self.BLOCK - height, self.BLOCK, height),
                )
                if blink:
                    painter.fillRect(place, QColor(255, 255, 255, 40))


# --- Иконки миров ---


def _rounded(pixmap: QPixmap, size: int, radius: int = 8) -> QPixmap:
    """Картинка size×size со скруглёнными углами (пиксели не сглаживаются)."""
    scaled = pixmap.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return result


def default_world_icon(size: int) -> QPixmap:
    """Иконка мира, если у мира нет своей icon.png: блок травы на тёмной плитке."""
    tile = QPixmap(size, size)
    tile.fill(QColor("#1c2a1e"))
    painter = QPainter(tile)
    block = iso_block(size * 3 // 4)
    painter.drawPixmap((size - block.width()) // 2, (size - block.height()) // 2, block)
    painter.end()
    return _rounded(tile, size)


def world_icon_from_png(data: bytes, size: int) -> QPixmap | None:
    """Иконка мира из icon.png (увеличивается без сглаживания, со скруглёнными углами)."""
    pixmap = QPixmap()
    if not pixmap.loadFromData(data, "PNG"):
        return None
    return _rounded(pixmap, size)


def logo_icon() -> QIcon:
    """Значок окна программы."""
    return QIcon(iso_block(64))


# --- «Достижение» после синхронизации ---


class AchievementToast(QFrame):
    """Плашка, которая выезжает справа сверху, как достижение в Майнкрафте, и сама уезжает."""

    WIDTH = 360
    MARGIN = 20
    SHOW_MS = 5000
    SLIDE_MS = 350

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(f"""
            QFrame#toast {{
                background: #18212d; border: 1px solid {rgba(Colors.ACCENT, 0.7)}; border-radius: 12px;
            }}
            QFrame#toast QLabel {{ background: transparent; border: none; }}
        """)
        block = QLabel()
        block.setPixmap(iso_block(44))
        self.title = QLabel()
        self.title.setFont(pixel_font(10))
        self.title.setStyleSheet(f"color: {Colors.ACCENT.name()};")
        self.text = QLabel()
        self.text.setWordWrap(True)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        texts.addWidget(self.title)
        texts.addWidget(self.text)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 16, 12)
        layout.setSpacing(14)
        layout.addWidget(block)
        layout.addLayout(texts, 1)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)

        self._animation = QPropertyAnimation(self, b"pos", self)
        self._animation.setDuration(self.SLIDE_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._animation_finished)
        self._leaving = False  # плашка уезжает: после анимации её нужно спрятать
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._slide_out)
        self.hide()

    def show_message(self, title: str, text: str) -> None:
        self.title.setText(title)
        self.text.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        y = self.MARGIN
        self._animation.stop()
        self._animation.setStartValue(QPoint(parent.width(), y))
        self._animation.setEndValue(QPoint(parent.width() - self.width() - self.MARGIN, y))
        self._leaving = False
        self.move(parent.width(), y)
        self.show()
        self.raise_()
        self._animation.start()
        self._hide_timer.start(self.SHOW_MS)

    def _slide_out(self) -> None:
        self._animation.stop()
        self._animation.setStartValue(self.pos())
        self._animation.setEndValue(QPoint(self.parentWidget().width(), self.y()))
        self._leaving = True
        self._animation.start()

    def _animation_finished(self) -> None:
        if self._leaving:
            self.hide()
