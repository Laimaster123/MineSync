"""Пиксельные текстуры блоков, нарисованные кодом (оригинальные текстуры Mojang не используются).

Каждая текстура — картинка 16×16 пикселей. Цвет каждого пикселя выбирается
из небольшой палитры «случайно», но с постоянным зерном (seed), поэтому
узор всегда один и тот же. При показе картинка увеличивается без сглаживания,
и пиксели остаются чёткими квадратами.
"""

import random
from functools import cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap

TEXTURE_SIZE = 16

# Палитры: (цвет, вес). Чем больше вес, тем чаще встречается цвет.
DIRT = [("#7a5638", 5), ("#6a4a30", 4), ("#8a6444", 3), ("#58402a", 2), ("#9a7452", 1)]
GRASS = [("#5f9e3c", 5), ("#4f8c30", 4), ("#72b44a", 3), ("#3f7426", 1)]
STONE = [("#7c7c7c", 5), ("#6c6c6c", 4), ("#8a8a8a", 3), ("#5c5c5c", 2)]
DEEP_STONE = [("#4a4a50", 5), ("#3e3e44", 4), ("#56565c", 3), ("#323236", 2)]


def _noise(palette: list[tuple[str, int]], seed: int) -> QImage:
    """Квадрат 16×16 из пикселей палитры."""
    rng = random.Random(seed)
    colors = [QColor(color) for color, _ in palette]
    weights = [weight for _, weight in palette]
    image = QImage(TEXTURE_SIZE, TEXTURE_SIZE, QImage.Format.Format_RGB32)
    for y in range(TEXTURE_SIZE):
        for x in range(TEXTURE_SIZE):
            image.setPixelColor(x, y, rng.choices(colors, weights)[0])
    return image


def dirt(seed: int = 1) -> QImage:
    return _noise(DIRT, seed)


def stone(seed: int = 2) -> QImage:
    image = _noise(STONE, seed)
    # Тёмные «трещинки»: несколько коротких горизонтальных штрихов.
    rng = random.Random(seed + 100)
    crack = QColor("#4e4e4e")
    for _ in range(5):
        x, y = rng.randrange(TEXTURE_SIZE - 3), rng.randrange(TEXTURE_SIZE)
        for dx in range(rng.randint(2, 3)):
            image.setPixelColor(x + dx, y, crack)
    return image


def deep_stone(seed: int = 3) -> QImage:
    return _noise(DEEP_STONE, seed)


def grass_side(seed: int = 4) -> QImage:
    """Блок травы сбоку: сверху зелёная полоса, неровно «стекающая» на землю."""
    image = dirt(seed)
    green = _noise(GRASS, seed + 50)
    rng = random.Random(seed + 200)
    for x in range(TEXTURE_SIZE):
        depth = 3 + rng.choice([0, 0, 1, 1, 2, 3])
        for y in range(depth):
            image.setPixelColor(x, y, green.pixelColor(x, y))
    return image


def grass_top(seed: int = 5) -> QImage:
    return _noise(GRASS, seed)


@cache
def scaled(name: str, scale: int, seed: int = 0) -> QPixmap:
    """Текстура, увеличенная в scale раз без сглаживания. Результат запоминается."""
    makers = {"dirt": dirt, "stone": stone, "deep_stone": deep_stone, "grass_side": grass_side, "grass_top": grass_top}
    image = makers[name](seed) if seed else makers[name]()
    size = TEXTURE_SIZE * scale
    return QPixmap.fromImage(
        image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    )
