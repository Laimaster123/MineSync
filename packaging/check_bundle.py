"""Проверить собранную программу перед публикацией: нет ли внутри личных слов (имя, почта, пути).

Запуск (слова — какие искать, регистр не важен):
    Windows: .venv\\Scripts\\python packaging\\check_bundle.py "Иван" "ivan@mail.ru"
    macOS:   .venv/bin/python packaging/check_bundle.py "Иван" "ivan@mail.ru"

Проверяются все файлы в dist/ и код, спрятанный внутри файла программы (архив PyInstaller).
"""

import marshal
import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    # В консоли английской Windows по умолчанию кодировка без русских букв.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    words = [word.lower() for word in sys.argv[1:]]
    if not words:
        print(__doc__)
        return 2
    needles = {word: [word.encode("utf-8"), word.encode("utf-16-le")] for word in words}
    found: list[str] = []
    for path in sorted((ROOT / "dist").rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes().lower()
        found += [f"{path.relative_to(ROOT)}: «{word}»" for word, forms in needles.items()
                  if any(form in data for form in forms)]
        if path.name in ("MineSync", "MineSync.exe"):
            found += _check_embedded(path, needles)
    if found:
        print("Найдено — перед публикацией разберитесь:")
        print("\n".join(found))
        return 1
    print("Чисто: личных слов в сборке не найдено.")
    return 0


def _check_embedded(executable: Path, needles: dict[str, list[bytes]]) -> list[str]:
    """Код внутри файла программы хранится сжатым — распаковываем и проверяем."""
    found = []
    archive = CArchiveReader(str(executable))
    for name in list(archive.toc):
        if not name.startswith("PYZ"):
            continue
        embedded = archive.open_embedded_archive(name)
        for module in embedded.toc:
            try:
                raw = marshal.dumps(embedded.extract(module)).lower()
            except Exception:  # noqa: BLE001 — служебные записи архива пропускаем
                continue
            found += [f"{executable.name} → {module}: «{word}»" for word, forms in needles.items()
                      if any(form in raw for form in forms)]
    return found


if __name__ == "__main__":
    sys.exit(main())
