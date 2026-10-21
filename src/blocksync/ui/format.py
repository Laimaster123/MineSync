"""Как показывать размеры, даты, длительность и количество: «7,5 ГБ», «20.10.2026 23:10», «1 мин 5 с», «3 файла»."""

from datetime import datetime

from blocksync.ui.strings import current_language, tr

_UNITS = (("size.gb", 1024**3), ("size.mb", 1024**2), ("size.kb", 1024))


def format_size(size_bytes: int) -> str:
    for key, factor in _UNITS:
        if size_bytes >= factor:
            value = size_bytes / factor
            # До 10 показываем одну цифру после запятой: «7,5 ГБ», дальше — целые: «325 МБ».
            text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
            return tr(key, value=text.replace(".", ","))
    return tr("size.bytes", value=size_bytes)


def format_time(time_ns: int | None) -> str:
    if time_ns is None:
        return "—"
    return datetime.fromtimestamp(time_ns / 1e9).strftime("%d.%m.%Y %H:%M")


def format_duration(seconds: float) -> str:
    total = max(1, round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return tr("duration.minutes", minutes=minutes, seconds=secs)
    return tr("duration.seconds", seconds=secs)


def format_play_time(ticks: int) -> str:
    """Время в мире по игровым тикам (20 тиков = 1 секунда): «1 ч 38 мин», «12 мин»."""
    minutes = ticks // 20 // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return tr("duration.hours", hours=hours, minutes=minutes)
    return tr("duration.minutes_only", minutes=max(1, minutes))


def format_count(count: int, key: str) -> str:
    """Число со словом в нужной форме: 1 файл, 3 файла, 12 файлов.

    В файле строк слово записано тремя формами через «|»: "файл|файла|файлов"
    (в английском: "file|files|files" — нужны только первая и последняя).
    """
    one, few, many = tr(key).split("|")
    if current_language() == "en":
        return f"{count} {one if count == 1 else many}"
    if count % 10 == 1 and count % 100 != 11:
        word = one
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        word = few
    else:
        word = many
    return f"{count} {word}"
