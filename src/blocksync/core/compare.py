"""Сравнение двух манифестов: что нужно сделать, чтобы получатель стал копией эталона."""

from dataclasses import dataclass, field

from blocksync.core.manifest import FileEntry, Manifest

# Допуск по времени изменения: 2 секунды. Разные файловые системы
# (NTFS на Windows, APFS на Mac) хранят время с разной точностью.
MTIME_TOLERANCE_NS: int = 2_000_000_000


@dataclass
class SyncPlan:
    """План синхронизации: результат сравнения эталона и получателя."""

    to_copy: list[FileEntry] = field(default_factory=list)  # новые и изменённые (записи эталона)
    to_delete: list[FileEntry] = field(default_factory=list)  # лишние у получателя
    unchanged: list[FileEntry] = field(default_factory=list)  # совпадают (записи получателя)

    @property
    def bytes_to_copy(self) -> int:
        """Сколько байт нужно передать с эталона."""
        return sum(entry.size for entry in self.to_copy)

    @property
    def has_changes(self) -> bool:
        """Есть ли хоть одно различие."""
        return bool(self.to_copy or self.to_delete)


def is_same_file(a: FileEntry, b: FileEntry) -> bool:
    """Одинаковы ли файлы: совпадает размер и время отличается не больше допуска."""
    return a.size == b.size and abs(a.mtime_ns - b.mtime_ns) <= MTIME_TOLERANCE_NS


def compare(source: Manifest, target: Manifest) -> SyncPlan:
    """Сравнить манифест эталона (source) и получателя (target)."""
    plan = SyncPlan()
    for path in sorted(source):
        source_entry = source[path]
        target_entry = target.get(path)
        if target_entry is not None and is_same_file(source_entry, target_entry):
            plan.unchanged.append(target_entry)
        else:
            plan.to_copy.append(source_entry)
    for path in sorted(target):
        if path not in source:
            plan.to_delete.append(target[path])
    return plan
