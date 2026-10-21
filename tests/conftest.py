"""Общие настройки тестов."""

import os

# Окна в тестах не должны появляться на экране.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
