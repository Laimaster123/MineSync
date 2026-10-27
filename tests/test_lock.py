from pathlib import Path

from blocksync.core.lock import is_world_locked

from helpers import hold_lock, make_world


def test_world_without_lock_file_is_free(tmp_path: Path) -> None:
    world = make_world(tmp_path / "w", {"level.dat": b"x"})
    assert not is_world_locked(world)


def test_lock_file_not_held_is_free(tmp_path: Path) -> None:
    world = make_world(tmp_path / "w", {"level.dat": b"x", "session.lock": "☃".encode()})
    assert not is_world_locked(world)


def test_lock_held_by_other_process_is_detected(tmp_path: Path) -> None:
    world = make_world(tmp_path / "w", {"level.dat": b"x", "session.lock": "☃".encode()})

    with hold_lock(world / "session.lock"):
        assert is_world_locked(world)

    # Игра закрыта — мир снова свободен, а файл не изменён.
    assert not is_world_locked(world)
    assert (world / "session.lock").read_bytes() == "☃".encode()
