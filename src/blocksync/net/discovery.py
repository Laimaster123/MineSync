"""Обнаружение устройств в локальной сети через mDNS (zeroconf, он же Bonjour).

Каждый запущенный MineSync «объявляет» в сети сервис _blocksync._tcp.local.
со своим ID, именем и портом, а также слушает такие же объявления других.
Так же находят друг друга AirPlay-колонки и сетевые принтеры.
"""

import ipaddress
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

import ifaddr
from zeroconf import IPVersion, NonUniqueNameException, ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

from blocksync.config.app_lock import AlreadyRunningError
from blocksync.config.settings import PairedDevice

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_blocksync._tcp.local."
PROTOCOL_VERSION = "1"
# Сколько ждать подробностей о найденном сервисе (миллисекунды).
RESOLVE_TIMEOUT_MS = 3000


# Диапазоны адресов домашних и офисных сетей. Другие адреса (например,
# 198.18.x.x у VPN-программ) объявлять и использовать нельзя: соединения туда
# уходят в VPN и не доходят до второго компьютера.
LAN_NETWORKS = [
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
]


@dataclass(frozen=True)
class FoundDevice:
    """Устройство MineSync, найденное в сети."""

    device_id: str
    name: str
    addresses: tuple[str, ...]  # IPv4-адреса, например ("192.168.1.20",)
    port: int

    @property
    def address(self) -> str:
        """Лучший адрес для подключения: из той же сети, что и этот компьютер."""
        return choose_address(self.addresses, local_networks())

    @property
    def base_url(self) -> str:
        return f"https://{self.address}:{self.port}"


class AlreadyRunningError(Exception):
    """В сети уже объявлено устройство с таким же ID: MineSync запущен второй раз."""


class PeerStatus(Enum):
    NOT_PAIRED = "not_paired"  # пары нет
    ONLINE = "online"  # сопряжено, устройство в сети
    OFFLINE = "offline"  # сопряжено, но устройство сейчас не видно


def peer_status(paired: PairedDevice | None, found: list[FoundDevice]) -> PeerStatus:
    if paired is None:
        return PeerStatus.NOT_PAIRED
    if any(device.device_id == paired.device_id for device in found):
        return PeerStatus.ONLINE
    return PeerStatus.OFFLINE


def is_lan_address(address: str) -> bool:
    ip = ipaddress.IPv4Address(address)
    return any(ip in network for network in LAN_NETWORKS)


def local_networks() -> list[ipaddress.IPv4Interface]:
    """Адреса этого компьютера вместе с размером сети, например 192.168.1.5/24."""
    result: list[ipaddress.IPv4Interface] = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if isinstance(ip.ip, str):  # IPv6 приходит кортежем — пропускаем
                result.append(ipaddress.IPv4Interface(f"{ip.ip}/{ip.network_prefix}"))
    return result


def local_ipv4_addresses() -> list[str]:
    """Адреса этого компьютера, которые объявляем в сети: только домашние сети."""
    addresses = [str(i.ip) for i in local_networks() if is_lan_address(str(i.ip))]
    return addresses or ["127.0.0.1"]


def choose_address(addresses: tuple[str, ...], local: list[ipaddress.IPv4Interface]) -> str:
    """Выбрать адрес устройства: сначала из нашей же сети, потом любой домашний."""
    lan = [a for a in addresses if is_lan_address(a)]
    # Наши домашние сети (сеть VPN сюда не входит, даже если VPN включён).
    our_networks = [i.network for i in local if is_lan_address(str(i.ip))]
    for address in lan:
        if any(ipaddress.IPv4Address(address) in network for network in our_networks):
            return address
    return (lan or list(addresses))[0]


class Discovery:
    """Объявляет это устройство в сети и следит за другими устройствами MineSync.

    on_change вызывается (из фонового потока), когда список найденных устройств меняется.
    """

    def __init__(
        self,
        device_id: str,
        device_name: str,
        port: int,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.device_id = device_id
        self.device_name = device_name
        self.port = port
        self.on_change = on_change
        self._found: dict[str, FoundDevice] = {}  # имя сервиса -> устройство
        self._lock = threading.Lock()
        self._zeroconf: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._info: ServiceInfo | None = None

    def start(self) -> None:
        self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        self._info = ServiceInfo(
            SERVICE_TYPE,
            f"{self.device_id}.{SERVICE_TYPE}",
            port=self.port,
            properties={"id": self.device_id, "name": self.device_name, "v": PROTOCOL_VERSION},
            server=f"blocksync-{self.device_id[:12]}.local.",
            parsed_addresses=local_ipv4_addresses(),
        )
        try:
            self._zeroconf.register_service(self._info)
        except NonUniqueNameException:
            # Запасная проверка: устройство с таким ID уже объявлено в сети (основная — AppLock).
            self._zeroconf.close()
            self._zeroconf = None
            raise AlreadyRunningError("MineSync уже запущен на этом компьютере") from None
        self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, handlers=[self._on_service_change])
        logger.info("Обнаружение запущено, адреса: %s", self._info.parsed_addresses())

    def stop(self) -> None:
        if self._zeroconf is None:
            return
        if self._browser is not None:
            self._browser.cancel()
        if self._info is not None:
            self._zeroconf.unregister_service(self._info)
        self._zeroconf.close()
        self._zeroconf = None

    def devices(self) -> list[FoundDevice]:
        """Найденные устройства (кроме этого)."""
        with self._lock:
            return sorted(self._found.values(), key=lambda d: d.name)

    def find(self, device_id: str) -> FoundDevice | None:
        return next((d for d in self.devices() if d.device_id == device_id), None)

    def _on_service_change(
        self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if state_change is ServiceStateChange.Removed:
            with self._lock:
                changed = self._found.pop(name, None) is not None
        else:
            device = self._resolve(zeroconf, service_type, name)
            if device is None:
                return
            with self._lock:
                changed = self._found.get(name) != device
                self._found[name] = device
        if changed:
            logger.info("Список устройств в сети изменился: %s", self.devices())
            if self.on_change is not None:
                self.on_change()

    def _resolve(self, zeroconf: Zeroconf, service_type: str, name: str) -> FoundDevice | None:
        """Узнать адрес, порт и свойства найденного сервиса."""
        info = zeroconf.get_service_info(service_type, name, timeout=RESOLVE_TIMEOUT_MS)
        if info is None or info.port is None:
            return None
        props = {k.decode(): (v or b"").decode() for k, v in info.properties.items()}
        device_id = props.get("id", "")
        if not device_id or device_id == self.device_id:
            return None  # это мы сами или чужой сервис
        addresses = tuple(info.parsed_addresses(IPVersion.V4Only))
        if not addresses:
            return None
        return FoundDevice(device_id, props.get("name", "?"), addresses, info.port)
