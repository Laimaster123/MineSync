"""Сервер без окна — только для разработки и тестов.

Позволяет запустить два экземпляра на одном компьютере, например:

  python -m blocksync.net.devserver --saves /tmp/pc/saves --data /tmp/pc/app \\
      --device-id pc --peer-id mac --key <ключ> --port 8001
  python -m blocksync.net.devserver --saves /tmp/mac/saves --data /tmp/mac/app \\
      --device-id mac --peer-id pc --key <ключ> --port 8002 --peer-url http://127.0.0.1:8001

Когда второе устройство просит «забери мир X у меня», этот экземпляр
скачивает мир с --peer-url. Ключ — 64 шестнадцатеричных символа (32 байта).
"""

import argparse
import logging
import threading
from pathlib import Path

from blocksync.core.backups import DEFAULT_BACKUPS
from blocksync.core.staging import cleanup_leftovers
from blocksync.net.auth import RequestVerifier
from blocksync.net.client import PeerClient
from blocksync.net.protocol import SyncRequest
from blocksync.net.pull import pull_world
from blocksync.net.server import SyncServer


def main() -> None:
    parser = argparse.ArgumentParser(description="MineSync: сервер без окна для разработки")
    parser.add_argument("--saves", type=Path, required=True, help="папка с мирами")
    parser.add_argument("--data", type=Path, required=True, help="папка для временных файлов и бэкапов")
    parser.add_argument("--device-id", required=True, help="ID этого устройства")
    parser.add_argument("--peer-id", required=True, help="ID сопряжённого устройства")
    parser.add_argument("--key", required=True, help="общий ключ в шестнадцатеричном виде")
    parser.add_argument("--port", type=int, default=0, help="порт (0 — любой свободный)")
    parser.add_argument("--peer-url", help="адрес второго устройства, у которого забирать миры")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    key = bytes.fromhex(args.key)
    tmp_root, backups_root = args.data / "tmp", args.data / "backups"
    cleanup_leftovers(tmp_root)

    def pull(world: str) -> None:
        with PeerClient(args.peer_url, args.device_id, key) as client:
            try:
                result = pull_world(client, world, args.saves, tmp_root, backups_root, DEFAULT_BACKUPS)
            except Exception as error:
                print(f"error {world}: {error}", flush=True)
                return
        print(f"synced {world}: файлов скопировано {result.copied_files}", flush=True)

    def on_sync_request(device_id: str, request: SyncRequest) -> None:
        world = request.world
        if args.peer_url is None:
            print(f"error {world}: не задан --peer-url", flush=True)
            return
        # Отвечаем сразу, а мир забираем в отдельном потоке.
        threading.Thread(target=pull, args=(world,), daemon=True).start()

    verifier = RequestVerifier(lambda device: key if device == args.peer_id else None)
    server = SyncServer(args.saves, verifier, port=args.port, on_sync_request=on_sync_request)
    server.start()
    print(f"port {server.port}", flush=True)
    try:
        threading.Event().wait()  # ждём Ctrl+C
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
