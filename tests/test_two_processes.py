"""Два настоящих экземпляра (отдельные процессы) на одном компьютере.

Сценарий: «ПК» просит «Мак» забрать мир, «Мак» скачивает его с «ПК».
"""

import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from blocksync.net.client import PeerClient
from helpers import make_level_dat, make_world, read_world


@contextmanager
def devserver(*args: str) -> Iterator[tuple[subprocess.Popen[str], int]]:
    """Запустить devserver отдельным процессом и узнать его порт."""
    process = subprocess.Popen(
        [sys.executable, "-m", "blocksync.net.devserver", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        # Явная кодировка с обеих сторон: иначе на Windows дочерний процесс и тест
        # могут взять разные (cp1251 / utf-8) и русские строки не совпадут.
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        assert process.stdout is not None
        first_line = process.stdout.readline().split()
        assert first_line[0] == "port", first_line
        yield process, int(first_line[1])
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_world_transferred_between_two_processes(tmp_path: Path) -> None:
    key = secrets.token_hex(32)
    pc_saves, mac_saves = tmp_path / "pc" / "saves", tmp_path / "mac" / "saves"
    world = make_world(pc_saves / "Мир для теста", {"region/r.0.0.mca": b"r" * 3_000_000, "data/x.dat": b"x"})
    make_level_dat(world)
    common = ["--key", key]

    with devserver("--saves", str(pc_saves), "--data", str(tmp_path / "pc" / "app"),
                   "--device-id", "pc", "--peer-id", "mac", *common) as (_pc, pc_port):
        with devserver("--saves", str(mac_saves), "--data", str(tmp_path / "mac" / "app"),
                       "--device-id", "mac", "--peer-id", "pc", *common,
                       "--peer-url", f"http://127.0.0.1:{pc_port}") as (mac, mac_port):
            # Пользователь на «ПК» нажал «Синхронизировать» и выбрал «играл на этом компьютере».
            with PeerClient(f"http://127.0.0.1:{mac_port}", "pc", bytes.fromhex(key)) as client:
                client.request_sync("Мир для теста")

            # «Мак» печатает строку, когда закончит (или "error ..." при ошибке).
            assert mac.stdout is not None
            line = mac.stdout.readline().strip()
            assert line.startswith("synced Мир для теста"), line

    assert read_world(mac_saves / "Мир для теста") == read_world(world)
