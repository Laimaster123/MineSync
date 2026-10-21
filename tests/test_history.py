"""История синхронизаций."""

from pathlib import Path

from blocksync.config import history
from blocksync.config.history import Direction, add_record, load_history, new_record, stats


def test_records_are_saved_newest_first(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    assert load_history(path) == []
    first = history.SyncRecord("2026-10-20T10:00:00", "TEST", Direction.RECEIVED, "ПК", 152, 0, 325_000_000, 9.2)
    add_record(path, first)
    add_record(path, new_record("Мой мир", Direction.SENT, "ПК"))

    records = load_history(path)
    assert [r.world for r in records] == ["Мой мир", "TEST"]
    assert records[1] == first

    summary = stats(records)
    assert (summary.received, summary.sent, summary.bytes_received) == (1, 1, 325_000_000)
    assert summary.last is not None and summary.last.world == "Мой мир"


def test_history_is_limited(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(history, "MAX_RECORDS", 3)
    path = tmp_path / "history.json"
    for index in range(5):
        add_record(path, history.SyncRecord(f"2026-10-20T10:00:0{index}", f"Мир {index}", Direction.RECEIVED, "ПК"))
    assert [r.world for r in load_history(path)] == ["Мир 4", "Мир 3", "Мир 2"]


def test_broken_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("это не JSON", encoding="utf-8")
    assert load_history(path) == []


def test_last_synced_by_folder_and_old_records() -> None:
    records = [
        history.SyncRecord("2026-10-21T22:00:00", "Мир", Direction.SENT, "ПК", folder="World", instance="a"),
        history.SyncRecord("2026-10-21T21:00:00", "Мир", Direction.RECEIVED, "ПК", folder="World", instance="b"),
        history.SyncRecord("2026-10-20T10:00:00", "Старый", Direction.RECEIVED, "ПК"),  # запись до сборок
    ]
    assert history.last_synced(records, "b", "World", "Мир").hour == 21  # та же папка, но другая сборка
    assert history.last_synced(records, "a", "World", "Мир").hour == 22
    assert history.last_synced(records, "a", "Old", "Старый").day == 20  # по названию мира
    assert history.last_synced(records, "a", "Нет", "Нет") is None
