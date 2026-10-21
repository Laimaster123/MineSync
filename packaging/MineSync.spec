# Описание сборки для PyInstaller. Запускать не напрямую, а через: python packaging/build.py
#
# Analysis — PyInstaller смотрит, какие модули импортирует программа, и собирает их.
# EXE      — файл запуска (MineSync.exe на Windows, MineSync внутри .app на Mac).
# COLLECT  — папка со всем нужным: Python, библиотеки, наши шрифты и тексты.
# BUNDLE   — только на macOS: оформляет папку как приложение MineSync.app.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT / "src"))
from blocksync import __version__  # noqa: E402

ICON = ROOT / "build" / "icon.png"  # её рисует build.py; PyInstaller сам переделает в .ico / .icns

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    # Ресурсы кладём в blocksync/resources — там их ищет resource_path() (см. resources/__init__.py).
    # Только шрифты и тексты: в __pycache__ лежат служебные файлы с путями компьютера, где шла сборка.
    datas=[
        (str(ROOT / "src" / "blocksync" / "resources" / folder), f"blocksync/resources/{folder}")
        for folder in ("fonts", "i18n")
    ],
    excludes=["tkinter", "pytest", "pytestqt", "PyInstaller"],
)
# Переводы самой библиотеки Qt не нужны: все тексты программы — свои (resources/i18n).
a.datas = [entry for entry in a.datas if "translations" not in entry[0].replace("\\", "/").split("/")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MineSync",
    console=False,  # без чёрного окна консоли
    icon=str(ICON),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MineSync", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="MineSync.app",
        icon=str(ICON),
        bundle_identifier="com.minesync.app",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "MineSync",
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            # Без этих двух строк macOS не даст искать второй компьютер в домашней сети.
            "NSLocalNetworkUsageDescription": "MineSync ищет второй компьютер в домашней сети и передаёт по ней миры Minecraft.",
            "NSBonjourServices": ["_blocksync._tcp"],
        },
    )
