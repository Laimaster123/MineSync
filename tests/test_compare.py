from blocksync.core.compare import compare
from blocksync.core.manifest import FileEntry, Manifest

SECOND_NS = 1_000_000_000


def manifest(*entries: FileEntry) -> Manifest:
    return {entry.path: entry for entry in entries}


def test_no_changes() -> None:
    files = manifest(FileEntry("level.dat", 10, 100 * SECOND_NS))

    plan = compare(files, files)

    assert not plan.has_changes
    assert [e.path for e in plan.unchanged] == ["level.dat"]
    assert plan.bytes_to_copy == 0


def test_missing_and_changed_files_are_copied() -> None:
    source = manifest(
        FileEntry("level.dat", 10, 100 * SECOND_NS),
        FileEntry("region/r.0.0.mca", 50, 200 * SECOND_NS),
        FileEntry("new.dat", 5, 100 * SECOND_NS),
    )
    target = manifest(
        FileEntry("level.dat", 10, 100 * SECOND_NS),
        FileEntry("region/r.0.0.mca", 40, 150 * SECOND_NS),
    )

    plan = compare(source, target)

    assert [e.path for e in plan.to_copy] == ["new.dat", "region/r.0.0.mca"]
    assert plan.bytes_to_copy == 55
    assert plan.to_delete == []


def test_extra_files_at_target_are_deleted() -> None:
    source = manifest(FileEntry("level.dat", 10, 100 * SECOND_NS))
    target = manifest(
        FileEntry("level.dat", 10, 100 * SECOND_NS),
        FileEntry("old.dat", 3, 100 * SECOND_NS),
    )

    plan = compare(source, target)

    assert [e.path for e in plan.to_delete] == ["old.dat"]
    assert plan.has_changes


def test_time_difference_within_tolerance_is_ignored() -> None:
    source = manifest(FileEntry("a", 10, 100 * SECOND_NS))
    one_second_later = manifest(FileEntry("a", 10, 101 * SECOND_NS))
    three_seconds_later = manifest(FileEntry("a", 10, 103 * SECOND_NS))

    assert not compare(source, one_second_later).has_changes
    assert compare(source, three_seconds_later).has_changes


def test_same_time_but_different_size_is_change() -> None:
    plan = compare(manifest(FileEntry("a", 10, 0)), manifest(FileEntry("a", 11, 0)))
    assert [e.path for e in plan.to_copy] == ["a"]
