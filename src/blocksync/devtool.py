"""Консольная программа для проверки сети без окон.

Запуск:  python -m blocksync.devtool
Для второго экземпляра на том же компьютере укажите другую папку данных:
         python -m blocksync.devtool --data /tmp/blocksync2 --name "Второй"
"""

import argparse
import logging
from pathlib import Path

from blocksync.config.paths import AppPaths
from blocksync.config.settings import load_settings, save_settings
from blocksync.core.world import list_worlds
from blocksync.net.client import NetworkError
from blocksync.net.discovery import PeerStatus
from blocksync.net.node import Node
from blocksync.net.pairing import PairingError

HELP = """Команды:
  status            кто я, какие устройства видно в сети, есть ли пара
  code              показать код сопряжения (вводится на другом устройстве)
  pair N КОД        ввести код, показанный на устройстве номер N из status
  worlds            миры на сопряжённом устройстве
  local             миры на этом устройстве и открыты ли они в игре
  forget            забыть сопряжённое устройство
  help              эта подсказка
  quit              выход"""

STATUS_TEXT = {
    PeerStatus.NOT_PAIRED: "не сопряжено",
    PeerStatus.ONLINE: "сопряжено, онлайн",
    PeerStatus.OFFLINE: "сопряжено, офлайн",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="MineSync: проверка сети без окон")
    parser.add_argument("--data", type=Path, help="папка данных приложения (по умолчанию стандартная)")
    parser.add_argument("--saves", type=Path, help="папка saves (запоминается в настройках)")
    parser.add_argument("--name", help="имя этого устройства (запоминается в настройках)")
    parser.add_argument("--port", type=int, default=0, help="порт сервера (0 — любой свободный)")
    parser.add_argument("--verbose", action="store_true", help="подробный лог")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s: %(message)s",
    )
    paths = AppPaths(args.data) if args.data else AppPaths.default()
    if args.saves or args.name:
        settings = load_settings(paths.settings_file)
        settings.saves_dir = args.saves or settings.saves_dir
        settings.device_name = args.name or settings.device_name
        save_settings(settings, paths.settings_file)

    node = Node(paths, port=args.port, on_sync_request=lambda request: print(f"\n[просьба забрать мир: {request.world}]"))
    node.start()
    print(f"MineSync запущен: «{node.settings.device_name}», порт {node.server.port}")
    print(HELP)
    try:
        while True:
            try:
                line = input("\n> ").strip()
            except EOFError:
                break
            if line in ("quit", "exit", "q"):
                break
            run_command(node, line)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()


def run_command(node: Node, line: str) -> None:
    command, *rest = line.split() or [""]
    try:
        if command == "status":
            print_status(node)
        elif command == "code":
            code = node.show_code()
            print(f"Код сопряжения: {code[:3]} {code[3:]}  (действует 2 минуты)")
        elif command == "pair" and len(rest) == 2:
            pair(node, rest[0], rest[1])
        elif command == "worlds":
            print_peer_worlds(node)
        elif command == "local":
            print_local_worlds(node)
        elif command == "forget":
            node.forget()
            print("Пара удалена.")
        elif command in ("help", ""):
            print(HELP)
        else:
            print("Неизвестная команда. Введите help.")
    except (NetworkError, PairingError) as error:
        print(f"Ошибка: {error}")


def print_status(node: Node) -> None:
    s = node.settings
    print(f"Это устройство: «{s.device_name}» (ID {s.device_id[:8]}…), порт {node.server.port}")
    print(f"Папка saves: {node.saves_dir}")
    paired = s.paired
    pair_text = f"«{paired.name}»" if paired else "нет"
    print(f"Пара: {pair_text} — {STATUS_TEXT[node.status()]}")
    devices = node.discovery.devices()
    if not devices:
        print("В сети других устройств MineSync не видно.")
    for number, device in enumerate(devices, start=1):
        mark = " (сопряжено)" if paired and paired.device_id == device.device_id else ""
        print(f"  {number}. «{device.name}» — {device.address}:{device.port}{mark}")


def pair(node: Node, number_text: str, code: str) -> None:
    devices = node.discovery.devices()
    if not number_text.isdigit() or not 1 <= int(number_text) <= len(devices):
        print("Нет устройства с таким номером. Введите status.")
        return
    paired = node.pair(devices[int(number_text) - 1], code.replace(" ", ""))
    print(f"Готово! Сопряжено с «{paired.name}».")


def print_peer_worlds(node: Node) -> None:
    client = node.peer_client()
    if client is None:
        print(f"Сопряжённое устройство недоступно ({STATUS_TEXT[node.status()]}).")
        return
    with client:
        worlds = client.list_worlds()
    if not worlds:
        print("На том устройстве миров нет (или не найдена папка saves).")
    for world in worlds:
        locked = " [открыт в игре]" if world.is_locked else ""
        print(f"  «{world.display_name}» — версия {world.version_name}, {world.size_bytes / 1e6:.1f} МБ{locked}")


def print_local_worlds(node: Node) -> None:
    try:
        worlds = list_worlds(node.saves_dir)
    except FileNotFoundError:
        print(f"Папка saves не найдена: {node.saves_dir}")
        return
    for world in worlds:
        state = "ОТКРЫТ В ИГРЕ" if world.is_locked else "свободен"
        print(f"  «{world.display_name}» — {state}")


if __name__ == "__main__":
    main()
