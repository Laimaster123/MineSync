"""Сетевой «узел» устройства: собирает вместе настройки, сервер, обнаружение и сопряжение.

Этим классом пользуются консольная программа devtool и интерфейс.
"""

import logging
import threading
from dataclasses import replace
from collections.abc import Callable
from pathlib import Path

from blocksync.config.app_lock import AppLock
from blocksync.config.instances import Instance, instance_from_saves, make_instance
from blocksync.config.paths import AppPaths, default_saves_dir
from blocksync.config.settings import PairedDevice, Settings, read_settings, save_settings
from blocksync.core.backups import validate_keep
from blocksync.core.staging import cleanup_leftovers
from blocksync.net.auth import RequestVerifier
from blocksync.net.client import NetworkError, PeerClient
from blocksync.net.discovery import Discovery, FoundDevice, PeerStatus, peer_status
from blocksync.net.pairing import PairingHost, pair_with
from blocksync.net.protocol import SyncRequest
from blocksync.net.server import SyncServer
from blocksync.net.tls import client_context, ensure_certificate, fingerprint, server_context

logger = logging.getLogger(__name__)

# Самое длинное описание мира (символов).
MAX_NOTE_LENGTH = 100


class Node:
    """Всё сетевое хозяйство одного устройства.

    paths: папки приложения (настройки, ключи, временные файлы).
    host: "0.0.0.0" — принимать соединения из локальной сети.
    on_change: вызывается из фонового потока, когда что-то изменилось
               (нашлось/пропало устройство, появилась или удалена пара).
    on_sync_request: вызывается, когда сопряжённое устройство просит забрать мир.

    Сборки (Prism и др.): у каждой своя папка saves. Связь «наша сборка ↔ сборка на
    втором устройстве» задаётся один раз и сообщается второму устройству.
    """

    def __init__(
        self,
        paths: AppPaths,
        host: str = "0.0.0.0",
        port: int = 0,
        on_change: Callable[[], None] | None = None,
        on_sync_request: Callable[[SyncRequest], None] | None = None,
    ) -> None:
        self.paths = paths
        self.on_change = on_change
        self.on_sync_request = on_sync_request
        self._settings_lock = threading.Lock()
        settings, broken = read_settings(paths.settings_file)
        self.settings: Settings = settings
        # Если файл настроек был повреждён — куда он отложен (интерфейс сообщит об этом).
        self.broken_settings: Path | None = broken
        self._migrate_saves_dir()

        cert_path, key_path = ensure_certificate(paths.tls_dir, self.settings.device_id)
        self.cert_pem = cert_path.read_text(encoding="ascii")
        self.pairing = PairingHost(
            self.settings.device_id, self.settings.device_name, self.cert_pem, self._remember_pair
        )
        self.server = SyncServer(
            self.saves_dir,
            RequestVerifier(self._key_for_device),
            host=host,
            port=port,
            on_sync_request=self._handle_sync_request,
            ssl_context=server_context(cert_path, key_path),
            on_unpair=self._handle_unpair,
            pairing=self.pairing,
            world_notes=self.world_notes,
            instances=lambda: [(i.id, i.name) for i in self.instances()],
            resolve_instance=self._resolve_instance,
            on_instance_link=self._handle_instance_link,
            on_peer_changed=lambda device_id: self._notify(),
        )
        self.discovery = self._new_discovery()
        self._lock = AppLock(paths.root)
        self._started = False

    # --- Свойства ---

    @property
    def saves_dir(self) -> Path:
        return self.settings.saves_dir or default_saves_dir()

    @property
    def backups_dir(self) -> Path:
        return self.settings.backups_dir or self.paths.default_backups_dir

    def backups_root_for(self, instance_id: str | None) -> Path:
        """Бэкапы хранятся отдельно для каждой сборки: <папка бэкапов>/<ID сборки>/<мир>/<дата>."""
        return self.backups_dir / instance_id if instance_id else self.backups_dir

    @property
    def paired(self) -> PairedDevice | None:
        return self.settings.paired

    def instances(self) -> list[Instance]:
        """Сборки, которые пользователь добавил на этом компьютере."""
        with self._settings_lock:
            return list(self.settings.instances)

    def instance(self, instance_id: str) -> Instance | None:
        return next((i for i in self.instances() if i.id == instance_id), None)

    def add_instance(self, folder: Path) -> Instance:
        """Добавить сборку по выбранной папке (ищет внутри папку с мирами — вызывать в фоне).

        Если не получилось, выбрасывает InstanceError с причиной.
        """
        instance = make_instance(folder, self.instances())
        with self._settings_lock:
            self.settings.instances.append(instance)
            save_settings(self.settings, self.paths.settings_file)
        logger.info("Добавлена сборка %s: %s", instance.name, instance.saves_dir)
        self._notify()
        return instance

    def remove_instance(self, instance_id: str) -> None:
        """Убрать сборку из списка (файлы не трогаются). Связь со вторым устройством тоже снимается."""
        if self.linked_instance(instance_id) is not None:
            try:
                self.link_instance(instance_id, None)
            except NetworkError as error:
                logger.warning("Не удалось сообщить второму устройству об удалении связи: %s", error)
                self._store_link(instance_id, None)
        with self._settings_lock:
            self.settings.instances = [i for i in self.settings.instances if i.id != instance_id]
            if self.settings.selected_tab == instance_id:
                self.settings.selected_tab = None
            save_settings(self.settings, self.paths.settings_file)
        self._notify()

    def _migrate_saves_dir(self) -> None:
        """Раньше была одна «папка saves» — превращаем её в первую сборку (один раз)."""
        saves = self.settings.saves_dir
        if self.settings.instances or saves is None or not saves.is_dir():
            return
        instance = instance_from_saves(saves)
        self.settings.instances.append(instance)
        self.settings.selected_tab = self.settings.selected_tab or instance.id
        self.settings.saves_dir = None  # теперь эту папку знает сборка
        save_settings(self.settings, self.paths.settings_file)
        logger.info("Папка saves %s добавлена как сборка «%s»", saves, instance.name)

    def linked_instance(self, instance_id: str) -> str | None:
        """Сборка на втором устройстве, связанная с нашей сборкой instance_id."""
        with self._settings_lock:
            return self.settings.instance_links.get(instance_id)

    def link_instance(self, instance_id: str, peer_instance: str | None, tell_peer: bool = True) -> None:
        """Связать нашу сборку со сборкой второго устройства (None — убрать связь).

        Второе устройство тоже запоминает связь (в обратную сторону), поэтому
        указывать её нужно только один раз, на любом из компьютеров. Работает с сетью —
        вызывать не из главного потока.
        """
        previous = self.linked_instance(instance_id)
        if tell_peer:
            client = self.peer_client()
            if client is not None:
                with client:
                    if previous is not None and previous != peer_instance:
                        client.send_instance_link(previous, None)
                    if peer_instance is not None:
                        client.send_instance_link(peer_instance, instance_id)
        self._store_link(instance_id, peer_instance)

    def _store_link(self, instance_id: str, peer_instance: str | None) -> None:
        with self._settings_lock:
            links = self.settings.instance_links
            if peer_instance is None:
                links.pop(instance_id, None)
            else:
                # Одна сборка второго устройства связана только с одной нашей.
                for other in [k for k, v in links.items() if v == peer_instance]:
                    del links[other]
                links[instance_id] = peer_instance
            save_settings(self.settings, self.paths.settings_file)
        self._notify()

    def notify_peer_changed(self) -> None:
        """Сообщить второму устройству, что здесь изменились миры (работает с сетью — вызывать в фоне)."""
        client = self.peer_client()
        if client is None:
            return
        with client:
            try:
                client.notify_changed()
            except NetworkError as error:
                logger.info("Не удалось сообщить второму устройству об изменениях: %s", error)

    def select_tab(self, tab: str) -> None:
        with self._settings_lock:
            self.settings.selected_tab = tab
            save_settings(self.settings, self.paths.settings_file)

    def set_language(self, language: str) -> None:
        with self._settings_lock:
            self.settings.language = language
            save_settings(self.settings, self.paths.settings_file)

    def status(self) -> PeerStatus:
        return peer_status(self.settings.paired, self.discovery.devices())

    # --- Запуск и остановка ---

    def start(self) -> None:
        # Сначала «замок»: вторая копия программы с той же папкой данных дальше не пойдёт.
        self._lock.acquire()
        try:
            cleanup_leftovers(self.paths.tmp_dir)
            self.server.start()
        except BaseException:
            self._lock.release()
            raise
        try:
            self.discovery.start()
        except BaseException:
            self.server.stop()
            self._lock.release()
            raise
        self._started = True
        logger.info(
            "Устройство %s (%s) запущено, порт %s, сертификат %s",
            self.settings.device_name, self.settings.device_id, self.server.port, fingerprint(self.cert_pem),
        )

    def stop(self) -> None:
        self.discovery.stop()
        self.server.stop()
        self._lock.release()
        self._started = False

    # --- Настройки ---

    def update_settings(
        self, device_name: str, saves_dir: Path | None, backups_dir: Path | None, keep_backups: int,
        full_backup: bool | None = None,
    ) -> None:
        """Сохранить новые настройки и сразу применить их к серверу и обнаружению."""
        validate_keep(keep_backups)
        with self._settings_lock:
            name_changed = device_name != self.settings.device_name
            self.settings.device_name = device_name
            self.settings.saves_dir = saves_dir
            self.settings.backups_dir = backups_dir
            self.settings.keep_backups = keep_backups
            if full_backup is not None:
                self.settings.full_backup = full_backup
            save_settings(self.settings, self.paths.settings_file)
        self.server.saves_dir = self.saves_dir
        self.pairing.device_name = device_name
        if name_changed and self._started:
            # Новое имя должно увидеть второе устройство: объявляем себя заново.
            self.discovery.stop()
            self.discovery = self._new_discovery()
            self.discovery.start()
        self._notify()

    def set_world_note(self, folder: str, note: str) -> None:
        """Сохранить описание мира (пустой текст — удалить описание)."""
        note = note.strip()[:MAX_NOTE_LENGTH]
        with self._settings_lock:
            if note:
                self.settings.world_notes[folder] = note
            else:
                self.settings.world_notes.pop(folder, None)
            save_settings(self.settings, self.paths.settings_file)

    def world_notes(self) -> dict[str, str]:
        with self._settings_lock:
            return dict(self.settings.world_notes)

    # --- Сопряжение ---

    def show_code(self) -> str:
        """Показать код на этом устройстве (его вводят на другом)."""
        return self.pairing.start_code()

    def pair(self, device: FoundDevice, code: str) -> PairedDevice:
        """Ввести код, показанный на устройстве device."""
        paired = pair_with(
            device.base_url, code, self.settings.device_id, self.settings.device_name, self.cert_pem
        )
        self._remember_pair(paired)
        return paired

    def forget(self) -> None:
        """Забыть сопряжённое устройство. Если оно в сети, сообщить и ему."""
        client = self.peer_client()
        if client is not None:
            with client:
                try:
                    client.unpair()
                except NetworkError as error:
                    logger.warning("Не удалось сообщить устройству о разрыве пары: %s", error)
        self._set_paired(None)

    # --- Работа с сопряжённым устройством ---

    def peer_client(self, instance: str | None = None) -> PeerClient | None:
        """Клиент для сопряжённого устройства или None, если его нет в сети.

        instance — сборка на том устройстве, с мирами которой будем работать.
        """
        paired = self.settings.paired
        if paired is None:
            return None
        device = self.discovery.find(paired.device_id)
        if device is None:
            return None
        return PeerClient(
            device.base_url, self.settings.device_id, paired.key, client_context(paired.cert_pem), instance
        )

    # --- Внутреннее ---

    def _new_discovery(self) -> Discovery:
        return Discovery(
            self.settings.device_id, self.settings.device_name, self.server.port, self._on_devices_changed
        )

    def _on_devices_changed(self) -> None:
        """Список устройств в сети изменился. Если сопряжённое устройство сменило имя — запомнить новое."""
        paired = self.settings.paired
        if paired is not None:
            device = self.discovery.find(paired.device_id)
            if device is not None and device.name and device.name != paired.name:
                logger.info("Сопряжённое устройство сменило имя: %s → %s", paired.name, device.name)
                with self._settings_lock:
                    current = self.settings.paired
                    if current is not None and current.device_id == paired.device_id:
                        self.settings.paired = replace(current, name=device.name)
                        save_settings(self.settings, self.paths.settings_file)
        self._notify()

    def _key_for_device(self, device_id: str) -> bytes | None:
        paired = self.settings.paired
        if paired is not None and paired.device_id == device_id:
            return paired.key
        return None

    def _remember_pair(self, paired: PairedDevice) -> None:
        logger.info("Сопряжено с %s (%s)", paired.name, paired.device_id)
        self._set_paired(paired)

    def _handle_unpair(self, device_id: str) -> None:
        logger.info("Устройство %s удалило пару", device_id)
        self._set_paired(None)

    def _handle_sync_request(self, device_id: str, request: SyncRequest) -> None:
        if self.on_sync_request is not None:
            self.on_sync_request(request)

    def _resolve_instance(self, instance_id: str) -> Path | None:
        instance = self.instance(instance_id)
        return instance.saves_dir if instance is not None else None

    def _handle_instance_link(self, device_id: str, instance_id: str, peer_instance: str | None) -> None:
        logger.info("Второе устройство связало нашу сборку %s со своей %s", instance_id, peer_instance)
        self._store_link(instance_id, peer_instance)

    def _set_paired(self, paired: PairedDevice | None) -> None:
        with self._settings_lock:
            self.settings.paired = paired
            save_settings(self.settings, self.paths.settings_file)
        self._notify()

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()
