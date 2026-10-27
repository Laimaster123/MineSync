"""Проверка обновлений: есть ли на GitHub версия программы новее этой.

При запуске (если это не выключено в настройках) программа один раз спрашивает
GitHub о последнем выпуске (release). Если его версия больше текущей, интерфейс
предлагает скачать архив для этой системы. Ничего, кроме номера версии,
программа о себе не отправляет.
"""

import logging
import re
import sys
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Репозиторий, где публикуются выпуски программы.
REPOSITORY = "Laimaster123/MineSync"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
TIMEOUT_SECONDS = 10.0

# Как называются архивы сборки для каждой системы (см. .github/workflows/build.yml).
_PLATFORM_NAMES = {"win32": "Windows", "darwin": "macOS"}
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")


@dataclass(frozen=True)
class Release:
    version: str  # например "1.2.0"
    page_url: str  # страница выпуска на GitHub
    download_url: str  # архив для этой системы, а если его нет — страница выпуска
    notes: str  # что нового (текст выпуска, Markdown)


def parse_version(text: str) -> tuple[int, ...] | None:
    """«v1.2.0» -> (1, 2, 0). Для предварительных версий («1.2.0-beta») и мусора — None."""
    match = _VERSION_RE.match(text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    # (1, 2) и (1, 2, 0) — одна и та же версия: дополняем нулями до одной длины.
    length = max(len(new), len(old))
    return new + (0,) * (length - len(new)) > old + (0,) * (length - len(old))


def release_from_json(data: dict[str, Any], platform: str = sys.platform) -> Release:
    version = str(data["tag_name"]).lstrip("v")
    page_url = str(data["html_url"])
    suffix = f"-{_PLATFORM_NAMES[platform]}.zip" if platform in _PLATFORM_NAMES else None
    download_url = page_url
    for asset in data.get("assets") or []:
        if suffix is not None and str(asset.get("name", "")).endswith(suffix):
            download_url = str(asset["browser_download_url"])
            break
    return Release(version, page_url, download_url, str(data.get("body") or ""))


def check_for_update(current: str, transport: httpx.BaseTransport | None = None) -> Release | None:
    """Последний выпуск, если он новее current; иначе None. Ошибки сети — тоже None (проверим в другой раз)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"MineSync/{current}"}
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport, follow_redirects=True) as http:
            response = http.get(LATEST_RELEASE_URL, headers=headers)
        if response.status_code != 200:
            logger.info("Проверка обновлений: GitHub ответил %s", response.status_code)
            return None
        data = response.json()
        if data.get("draft") or data.get("prerelease"):
            return None
        release = release_from_json(data)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
        logger.info("Не удалось проверить обновления: %s", error)
        return None
    if not is_newer(release.version, current):
        return None
    logger.info("Доступна новая версия %s (сейчас %s)", release.version, current)
    return release
