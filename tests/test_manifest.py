import os
import sys
import unicodedata
from pathlib import Path

import pytest

from blocksync.core.manifest import build_manifest, newest_mtime_ns, normalize_path, total_size

from helpers import BASE_TIME, make_world, write_file


def test_manifest_lists_files_with_size_and_time(tmp_path: Path) -> None:
    world = make_world(tmp_path / "w", {"level.dat": b"12345", "region/r.0.0.mca": b"ab"})

    manifest = build_manifest(world)

    assert set(manifest) == {"level.dat", "region/r.0.0.mca"}
    assert manifest["level.dat"].size == 5
    assert manifest["region/r.0.0.mca"].mtime_ns == BASE_TIME * 1_000_000_000
    assert total_size(manifest) == 7
    assert newest_mtime_ns(manifest) == BASE_TIME * 1_000_000_000


def test_manifest_skips_service_files(tmp_path: Path) -> None:
    world = make_world(
        tmp_path / "w",
        {"level.dat": b"x", "session.lock": b"x", ".DS_Store": b"x", "data/Thumbs.db": b"x"},
    )

    assert set(build_manifest(world)) == {"level.dat"}


@pytest.mark.skipif(sys.platform == "win32", reason="символические ссылки на Windows требуют прав")
def test_manifest_skips_symlinks(tmp_path: Path) -> None:
    world = make_world(tmp_path / "w", {"level.dat": b"x"})
    outside = tmp_path / "outside"
    write_file(outside / "secret.txt")
    os.symlink(outside / "secret.txt", world / "link.txt")
    os.symlink(outside, world / "linkdir")

    assert set(build_manifest(world)) == {"level.dat"}


def test_empty_manifest_has_no_newest_time(tmp_path: Path) -> None:
    world = make_world(tmp_path / "w", {})
    assert newest_mtime_ns(build_manifest(world)) is None


def test_normalize_path_uses_slash_and_nfc() -> None:
    nfd_name = unicodedata.normalize("NFD", "мой мир\\йод.txt")
    assert normalize_path(nfd_name) == unicodedata.normalize("NFC", "мой мир/йод.txt")


def test_manifest_cyrillic_nfd_name_becomes_nfc(tmp_path: Path) -> None:
    # Файл с именем в форме NFD, как его иногда создаёт macOS.
    nfd_name = unicodedata.normalize("NFD", "ёжик й.dat")
    world = make_world(tmp_path / "w", {f"data/{nfd_name}": b"x"})

    manifest = build_manifest(world)

    assert list(manifest) == [unicodedata.normalize("NFC", "data/ёжик й.dat")]
