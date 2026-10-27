"""Окна программы: тексты, список миров, синхронизация в обе стороны, настройки, бэкапы, сопряжение.

Два «устройства» (ПК и Мак) запускаются в одном процессе, каждое со своими
временными папками. Окна не показываются на экране (QT_QPA_PLATFORM=offscreen).
"""

import json
import re
import string
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from blocksync.config.history import Direction, SyncRecord, add_record, load_history
from blocksync.core.backups import list_backups
from blocksync.net import updates
from blocksync.net.node import Node
from blocksync.net.overview import WorldStatus
from blocksync.net.updates import Release
from blocksync.resources import resource_path
from blocksync.ui.backups_dialog import BackupsDialog, load_all_backups
from blocksync.ui.format import format_count, format_duration, format_play_time, format_size
from blocksync.ui.history_dialog import HistoryDialog
from blocksync.ui.main_window import MainWindow
from blocksync.ui.pairing_dialog import PairingDialog
from blocksync.ui.settings_dialog import SettingsDialog
from blocksync.ui.sync_dialog import SyncDialog
from blocksync.ui.update_dialog import UpdateDialog
from blocksync.ui.worker import NodeBridge

from helpers import BASE_TIME, hold_lock, make_level_dat, make_world, read_world, write_file
from test_discovery import wait_until
from test_instances import make_prism_instance
from test_node import make_node

TIMEOUT_MS = 20_000
UI_DIR = Path(__file__).resolve().parents[1] / "src" / "blocksync" / "ui"


# --- Тексты и форматирование ---


def test_all_text_keys_exist() -> None:
    strings = json.loads(resource_path("i18n/ru.json").read_text(encoding="utf-8"))
    code = "\n".join(path.read_text(encoding="utf-8") for path in UI_DIR.glob("*.py"))
    used = set(re.findall(r'"((?:common|size|duration|word|badge|history|note|toast|instances|language|status|main|sync|pairing|backups|settings|error)\.[a-z_]+)"', code))
    assert used, "ключи не найдены — проверьте регулярное выражение"
    assert sorted(used - set(strings)) == []
    for text in strings.values():
        list(string.Formatter().parse(text))  # шаблоны {имя} записаны без ошибок
    # В английском те же ключи и те же {подстановки}, что в русском.
    english = json.loads(resource_path("i18n/en.json").read_text(encoding="utf-8"))
    assert set(english) == set(strings)
    for key, text in strings.items():
        names = {name for _, name, _, _ in string.Formatter().parse(text) if name}
        assert names == {name for _, name, _, _ in string.Formatter().parse(english[key]) if name}, key


def test_format_size_and_duration() -> None:
    assert format_size(500) == "500 Б"
    assert format_size(325 * 1024**2) == "325 МБ"
    assert format_size(int(7.5 * 1024**3)) == "7,5 ГБ"
    assert format_duration(12.4) == "12 с"
    assert format_duration(65) == "1 мин 5 с"
    assert format_play_time(117404) == "1 ч 37 мин"
    assert format_play_time(100) == "1 мин"
    assert [format_count(n, "word.files") for n in (1, 3, 5, 11, 21, 112)] == [
        "1 файл", "3 файла", "5 файлов", "11 файлов", "21 файл", "112 файлов",
    ]


# --- Два устройства с окнами ---


@dataclass
class Device:
    node: Node
    bridge: NodeBridge
    saves: Path
    instance_id: str
    window: MainWindow | None = None

    def open_window(self, qtbot: QtBot) -> MainWindow:
        self.window = MainWindow(self.node, self.bridge)
        qtbot.addWidget(self.window)
        return self.window


def make_device(root: Path, name: str) -> Device:
    saves = root / "saves"
    saves.mkdir(parents=True)
    make_node(root / "app", name, saves)  # создаёт настройки с нужным именем и папкой saves
    bridge = NodeBridge()
    from blocksync.config.paths import AppPaths

    node = Node(AppPaths(root / "app"), on_change=bridge.changed.emit, on_sync_request=bridge.sync_requested.emit)
    # Папка saves из настроек сама стала единственной сборкой устройства — её и открываем.
    [instance] = node.instances()
    node.select_tab(instance.id)
    return Device(node, bridge, saves, instance.id)


@pytest.fixture
def devices(tmp_path: Path, qtbot: QtBot) -> Iterator[tuple[Device, Device]]:
    pc = make_device(tmp_path / "pc", "ПК")
    mac = make_device(tmp_path / "mac", "Мак")
    pc.node.start()
    mac.node.start()
    try:
        yield pc, mac
    finally:
        for device in (pc, mac):
            if device.window is not None:
                device.window.close()
            device.node.stop()


def pair_devices(pc: Device, mac: Device) -> None:
    assert wait_until(lambda: mac.node.discovery.find(pc.node.settings.device_id) is not None)
    assert wait_until(lambda: pc.node.discovery.find(mac.node.settings.device_id) is not None)
    found = mac.node.discovery.find(pc.node.settings.device_id)
    assert found is not None
    mac.node.pair(found, pc.node.show_code())
    # Связь сборок указывается на одном устройстве, второе запоминает её само.
    mac.node.link_instance(mac.instance_id, pc.instance_id)
    assert pc.node.linked_instance(pc.instance_id) == mac.instance_id


def add_world(device: Device, name: str, files: dict[str, bytes], mtime: float = BASE_TIME) -> Path:
    world = make_world(device.saves / name, files, mtime=mtime)
    make_level_dat(world, level_name=name)
    return world


def wait_for_rows(qtbot: QtBot, window: MainWindow, condition: "callable") -> None:  # type: ignore[valid-type]
    qtbot.waitUntil(lambda: window.view is not None and condition(window.view.rows), timeout=TIMEOUT_MS)


def test_main_window_shows_statuses(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    pc, mac = devices
    add_world(pc, "TEST", {"region/r.0.0.mca": b"x" * 5000})
    add_world(mac, "Мой мир", {"a": b"1"})
    window = mac.open_window(qtbot)
    assert window.windowTitle() == "MineSync"

    # Пока пары нет — только свои миры, статус неизвестен, синхронизировать нельзя.
    wait_for_rows(qtbot, window, lambda rows: [r.folder_name for r in rows] == ["Мой мир"])
    # Второго устройства нет — кнопка у мира серая.
    assert not window.world_list.row_for("Мой мир").sync_button.isEnabled()

    pair_devices(pc, mac)
    wait_for_rows(qtbot, window, lambda rows: {r.folder_name: r.status for r in rows} == {
        "TEST": WorldStatus.ONLY_THERE, "Мой мир": WorldStatus.ONLY_HERE,
    })
    assert "ПК" in window.peer_label.text()
    # Миры отличаются (есть только на одном устройстве) — кнопки зелёные.
    qtbot.waitUntil(lambda: window.world_list.row_for("TEST").sync_button.isEnabled(), timeout=TIMEOUT_MS)
    assert window.world_list.row_for("TEST").sync_button.property("variant") == "primary"


def test_pull_world_through_sync_dialog(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    """Мак: «играл на ПК» -> мир TEST с ПК появляется на Маке."""
    pc, mac = devices
    pc_world = add_world(pc, "TEST", {"region/r.0.0.mca": b"x" * 3_000_000, "data/мой.dat": b"1"})
    pair_devices(pc, mac)
    window = mac.open_window(qtbot)
    wait_for_rows(qtbot, window, lambda rows: len(rows) == 1 and rows[0].status is WorldStatus.ONLY_THERE)

    window.start_sync(window.world_list.row_for("TEST").row)
    dialog = window.sync_dialog
    assert dialog is not None
    # Мир есть только на ПК — подсказка сама отмечает «ПК», «Далее» становится активной.
    qtbot.waitUntil(lambda: dialog.peer_radio.isChecked(), timeout=TIMEOUT_MS)
    assert "только на «ПК»" in dialog.suggestion_label.text()
    assert dialog.next_button.isEnabled()
    dialog.next_button.click()

    qtbot.waitUntil(lambda: dialog.start_button.isEnabled(), timeout=TIMEOUT_MS)
    assert "Скопировать: 3 файла" in dialog.summary_label.text()
    dialog.start_button.click()

    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == 3, timeout=TIMEOUT_MS)
    assert "Готово" in dialog.result_label.text(), dialog.result_label.text()
    assert "Благодаря сжатию" in dialog.result_label.text()  # 3 МБ одинаковых байт сжимаются в разы
    assert read_world(mac.saves / "TEST") == read_world(pc_world)
    # Выехало «достижение».
    assert not window.toast.isHidden() and "TEST" in window.toast.text.text()

    dialog.accept()
    wait_for_rows(qtbot, window, lambda rows: rows[0].status is WorldStatus.SAME)
    # Теперь миры одинаковы — кнопка серая, синхронизировать нечего.
    assert not window.world_list.row_for("TEST").sync_button.isEnabled()


def test_push_world_opens_progress_on_receiver(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    """Мак: «играл здесь» -> ПК сам забирает мир и показывает прогресс."""
    pc, mac = devices
    mac_world = add_world(mac, "TEST", {"a": b"new version"}, mtime=BASE_TIME + 100)
    add_world(pc, "TEST", {"a": b"old", "b": b"x"})
    pair_devices(pc, mac)
    pc_window = pc.open_window(qtbot)
    mac_window = mac.open_window(qtbot)
    wait_for_rows(qtbot, mac_window, lambda rows: rows and rows[0].status is WorldStatus.DIFFERENT)

    mac_window.start_sync(mac_window.world_list.row_for("TEST").row)
    dialog = mac_window.sync_dialog
    assert dialog is not None
    dialog.here_radio.setChecked(True)
    dialog.next_button.click()
    qtbot.waitUntil(lambda: dialog.preview is not None, timeout=TIMEOUT_MS)
    # На ПК удаляется половина файлов мира — программа просит подтверждения.
    assert "удалено больше" in dialog.warnings_label.text()
    dialog.confirm_box.setChecked(True)
    dialog.start_button.click()
    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == 3, timeout=TIMEOUT_MS)
    assert "Запрос отправлен" in dialog.result_label.text(), dialog.result_label.text()

    # На ПК само открылось окно и выполнило передачу.
    qtbot.waitUntil(lambda: pc_window.sync_dialog is not None, timeout=TIMEOUT_MS)
    receiver = pc_window.sync_dialog
    assert receiver is not None
    qtbot.waitUntil(lambda: receiver.pages.currentIndex() == 3, timeout=TIMEOUT_MS)
    assert "Готово" in receiver.result_label.text(), receiver.result_label.text()
    assert read_world(pc.saves / "TEST") == read_world(mac_world)
    # Старая версия мира на ПК ушла в бэкап.
    assert len(list_backups(pc.node.backups_root_for(pc.instance_id), "TEST")) == 1
    # В истории: на Маке — «отправлено», на ПК — «получено» с подробностями.
    [sent] = load_history(mac.node.paths.history_file)
    [received] = load_history(pc.node.paths.history_file)
    assert sent.direction is Direction.SENT and sent.peer == "ПК"
    assert received.direction is Direction.RECEIVED and received.peer == "Мак" and received.deleted_files == 1

    history = HistoryDialog(pc.node)
    qtbot.addWidget(history)
    qtbot.waitUntil(lambda: history.tree.topLevelItemCount() == 1, timeout=TIMEOUT_MS)
    assert history.received_card.value.text() == "1"


def test_warning_needs_confirmation(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    pc, mac = devices
    add_world(pc, "TEST", {"a": b"old"}, mtime=BASE_TIME)
    add_world(mac, "TEST", {"a": b"newer on mac"}, mtime=BASE_TIME + 1000)
    pair_devices(pc, mac)
    window = mac.open_window(qtbot)
    wait_for_rows(qtbot, window, lambda rows: rows and rows[0].status is WorldStatus.DIFFERENT)

    window.start_sync(window.world_list.row_for("TEST").row)
    dialog = window.sync_dialog
    assert dialog is not None
    dialog.peer_radio.setChecked(True)  # «играл на ПК», хотя на Маке мир свежее
    dialog.next_button.click()
    qtbot.waitUntil(lambda: dialog.preview is not None, timeout=TIMEOUT_MS)

    assert "изменён позже" in dialog.warnings_label.text()
    assert not dialog.start_button.isEnabled()
    dialog.confirm_box.setChecked(True)
    assert dialog.start_button.isEnabled()
    dialog.reject()


def test_settings_dialog(devices: tuple[Device, Device], qtbot: QtBot, tmp_path: Path) -> None:
    _, mac = devices
    dialog = SettingsDialog(mac.node)
    qtbot.addWidget(dialog)

    # Папка бэкапов не может лежать внутри папки миров сборки.
    dialog.backups_edit.setText(str(mac.saves / "бэкапы"))
    assert "внутри" in (dialog.validate() or "")

    dialog.backups_edit.setText(str(tmp_path / "бэкапы"))
    dialog.name_edit.setText("Мой Мак")
    dialog.keep_spin.setValue(5)
    assert dialog.updates_box.isChecked()  # по умолчанию обновления проверяются
    dialog.updates_box.setChecked(False)
    dialog.save()

    assert mac.node.settings.device_name == "Мой Мак"
    assert mac.node.settings.keep_backups == 5
    assert mac.node.backups_dir == tmp_path / "бэкапы"
    assert not mac.node.settings.check_updates


def test_update_is_offered_after_sync_and_skipped_version_is_not(
    devices: tuple[Device, Device], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, mac = devices
    release = Release("9.0.0", "https://x", "https://x/a.zip", "")
    monkeypatch.setattr(updates, "check_for_update", lambda current, transport=None: release)
    window = mac.open_window(qtbot)

    window.check_updates()
    qtbot.waitUntil(lambda: _update_dialog(window) is not None, timeout=TIMEOUT_MS)
    dialog = _update_dialog(window)
    assert dialog is not None
    dialog.skip_button.click()

    window._offer_update(release)  # о пропущенной версии больше не напоминаем
    assert _update_dialog(window) is None


def _update_dialog(window: MainWindow) -> UpdateDialog | None:
    return next((d for d in window.findChildren(UpdateDialog) if d.isVisible()), None)


def test_backups_dialog_restores(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    _, mac = devices
    world = add_world(mac, "TEST", {"a": b"version 1"})
    backup_dir = mac.node.backups_root_for(mac.instance_id) / "TEST" / "2026-10-20_12-00-00"
    backup_dir.mkdir(parents=True)
    make_world(backup_dir, {"a": b"old version"})

    dialog = BackupsDialog(mac.node)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.tree.topLevelItemCount() == 1, timeout=TIMEOUT_MS)
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0).child(0))
    dialog.confirm = lambda text: True  # type: ignore[method-assign]
    dialog.restore_selected()

    qtbot.waitUntil(lambda: "восстановлен" in dialog.status_label.text(), timeout=TIMEOUT_MS)
    assert read_world(world) == {"a": b"old version"}
    # Версия до восстановления тоже сохранена.
    qtbot.waitUntil(lambda: dialog.tree.topLevelItem(0) is not None
                    and dialog.tree.topLevelItem(0).childCount() == 2, timeout=TIMEOUT_MS)


def test_pairing_dialog(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    pc, mac = devices
    assert wait_until(lambda: mac.node.discovery.find(pc.node.settings.device_id) is not None)
    pc_dialog = PairingDialog(pc.node, pc.bridge)
    mac_dialog = PairingDialog(mac.node, mac.bridge)
    qtbot.addWidget(pc_dialog)
    qtbot.addWidget(mac_dialog)

    pc_dialog.show_code()
    code = pc_dialog.code_label.text().replace(" ", "")
    assert len(code) == 6

    # В сети могут быть и другие экземпляры MineSync, поэтому выбираем ПК явно.
    pc_id = pc.node.settings.device_id
    qtbot.waitUntil(lambda: mac_dialog.device_combo.findData(pc_id) >= 0, timeout=TIMEOUT_MS)
    mac_dialog.device_combo.setCurrentIndex(mac_dialog.device_combo.findData(pc_id))
    mac_dialog.code_edit.setText(code)
    mac_dialog.pair_button.click()

    qtbot.waitUntil(lambda: "Готово" in mac_dialog.enter_result.text(), timeout=TIMEOUT_MS)
    assert mac.node.paired is not None and mac.node.paired.name == "ПК"
    # На ПК код исчез, и окно показывает новую пару.
    qtbot.waitUntil(lambda: "Мак" in pc_dialog.status_label.text(), timeout=TIMEOUT_MS)
    assert pc_dialog.code_label.text() == ""

    mac_dialog.forget()
    qtbot.waitUntil(lambda: mac.node.paired is None and pc.node.paired is None, timeout=TIMEOUT_MS)
    pc_dialog.reject()
    mac_dialog.reject()


def test_world_note_is_saved_and_shown_on_both_devices(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    pc, mac = devices
    add_world(pc, "TEST", {"a": b"1"})
    add_world(mac, "Мой", {"a": b"1"})
    pair_devices(pc, mac)
    pc.node.set_world_note("TEST", "  мир для экспериментов  ")
    mac.node.set_world_note("Мой", "основной")

    window = mac.open_window(qtbot)
    wait_for_rows(qtbot, window, lambda rows: len(rows) == 2 and all(r.status is not WorldStatus.UNKNOWN for r in rows))
    # Своё описание и описание, сделанное на ПК, видны в списке на Маке.
    qtbot.waitUntil(lambda: any(w.row.folder_name == "TEST" for w in window.world_list.rows.values()),
                    timeout=TIMEOUT_MS)
    assert window.world_list.row_for("Мой").note == "основной"
    assert window.world_list.row_for("TEST").note == "мир для экспериментов"

    mac.node.set_world_note("Мой", "")
    assert "Мой" not in mac.node.world_notes()
    # Описание хранится в настройках программы, папка мира не меняется.
    assert sorted(p.name for p in (mac.saves / "Мой").iterdir()) == ["a", "level.dat"]


def test_instances_link_recent_and_language(devices: tuple[Device, Device], qtbot: QtBot, tmp_path: Path) -> None:
    """У Мака две сборки Prism: первую связываем с папкой ПК, смотрим «Недавние», меняем язык."""
    pc, mac = devices
    prism = tmp_path / "mac" / "Prism"
    first = make_prism_instance(prism, "26.3", "26.3")
    second = make_prism_instance(prism, "mod", "Мой мод")
    make_level_dat(make_world(first / "minecraft/saves/Выживание", {"a": b"1"}, mtime=BASE_TIME + 50), level_name="Выживание")
    make_level_dat(make_world(second / "minecraft/saves/Модовый", {"a": b"1"}, mtime=BASE_TIME + 90), level_name="Модовый")
    vanilla = mac.node.add_instance(first)
    modded = mac.node.add_instance(second)
    assert (vanilla.name, modded.name) == ("26.3", "mod")
    add_world(pc, "TEST", {"a": b"1"})
    pair_devices(pc, mac)

    mac.node.select_tab(vanilla.id)
    window = mac.open_window(qtbot)
    # Сборка ещё не связана: статус «Не связано», предлагается выбрать сборку ПК.
    wait_for_rows(qtbot, window, lambda rows: [r.status for r in rows] == [WorldStatus.NOT_LINKED])
    qtbot.waitUntil(lambda: window.instance_bar.link_combo.isVisibleTo(window), timeout=TIMEOUT_MS)
    assert window.instance_bar.link_combo.findData(pc.instance_id) >= 0
    assert set(window.instance_bar.tabs) == {"recent", mac.instance_id, vanilla.id, modded.id}

    window.link_instance(vanilla.id, pc.instance_id)
    # Связь запомнили оба компьютера.
    # ПК запоминает связь, как только получает запрос, а Мак — после ответа ПК: ждём обоих.
    qtbot.waitUntil(lambda: pc.node.linked_instance(pc.instance_id) == vanilla.id, timeout=TIMEOUT_MS)
    qtbot.waitUntil(lambda: mac.node.linked_instance(vanilla.id) == pc.instance_id, timeout=TIMEOUT_MS)
    wait_for_rows(qtbot, window, lambda rows: {r.folder_name: r.status for r in rows} == {
        "Выживание": WorldStatus.ONLY_HERE, "TEST": WorldStatus.ONLY_THERE,
    })

    # «Недавние»: миры из всех сборок, сначала самый свежий.
    window.select_tab("recent")
    wait_for_rows(qtbot, window, lambda rows: [r.folder_name for r in rows][:2] == ["Модовый", "Выживание"])
    assert window.world_list.row_for("Модовый").row.instance_name == "mod"

    window.change_language("en")
    assert window.pair_button.text().strip() == "Pairing"
    assert window.instance_bar.tabs["recent"].text().strip() == "Recent"
    window.change_language("ru")
    assert window.pair_button.text().strip() == "Сопряжение"

    # Сборку убрали из списка: связь снята на обоих компьютерах, файлы на месте.
    mac.node.remove_instance(vanilla.id)
    assert vanilla.id not in {i.id for i in mac.node.instances()}
    assert pc.node.linked_instance(pc.instance_id) is None
    assert (first / "minecraft/saves/Выживание/level.dat").is_file()


def test_add_instance_errors_are_shown(devices: tuple[Device, Device], qtbot: QtBot, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    _, mac = devices
    window = mac.open_window(qtbot)
    shown: list[str] = []
    window.show_warning = lambda title, text: shown.append(text)  # type: ignore[method-assign]
    empty = tmp_path / "Пустая папка"
    empty.mkdir()
    monkeypatch.setattr("blocksync.ui.main_window.QFileDialog.getExistingDirectory", lambda *args: str(empty))

    window.add_instance()

    qtbot.waitUntil(lambda: bool(shown), timeout=TIMEOUT_MS)
    assert "не найдена папка с мирами" in shown[0] and "Пустая папка" in shown[0]


def test_old_backups_restore_into_matching_instance(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    """Бэкап, сделанный до появления сборок, восстанавливается в сборку, где есть мир с таким именем."""
    _, mac = devices
    world = add_world(mac, "TEST", {"a": b"new"})
    legacy = mac.node.backups_dir / "TEST" / "2026-10-20_23-55-34"
    legacy.mkdir(parents=True)
    make_world(legacy, {"a": b"old"})

    [group] = load_all_backups(mac.node)
    assert group.target == world and group.backups_root == mac.node.backups_dir

    dialog = BackupsDialog(mac.node)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.tree.topLevelItemCount() == 1, timeout=TIMEOUT_MS)
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0).child(0))
    dialog.confirm = lambda text: True  # type: ignore[method-assign]
    dialog.restore_selected()
    qtbot.waitUntil(lambda: "восстановлен" in dialog.status_label.text(), timeout=TIMEOUT_MS)
    assert read_world(world) == {"a": b"old"}


def test_window_notices_changes_by_itself(devices: tuple[Device, Device], qtbot: QtBot,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Без кнопки «Обновить»: сохранение мира на ПК видно на Маке, новый мир на Маке — сразу у Мака."""
    monkeypatch.setattr("blocksync.ui.change_watcher.SETTLE_MS", 200)
    pc, mac = devices
    pc_world = add_world(pc, "TEST", {"a": b"1"})
    add_world(mac, "TEST", {"a": b"1"})
    pair_devices(pc, mac)
    pc_window = pc.open_window(qtbot)
    mac_window = mac.open_window(qtbot)
    wait_for_rows(qtbot, mac_window, lambda rows: [r.status for r in rows] == [WorldStatus.SAME])
    wait_for_rows(qtbot, pc_window, lambda rows: [r.status for r in rows] == [WorldStatus.SAME])
    assert str(pc_world) in pc_window.watcher.watched()

    # «Игра сохранила мир» на ПК: ПК заметил сам и сообщил Маку.
    write_file(pc_world / "level.dat", b"saved by the game", mtime=BASE_TIME + 500)
    wait_for_rows(qtbot, mac_window, lambda rows: [r.status for r in rows] == [WorldStatus.DIFFERENT])

    # Новый мир на Маке появляется в его списке сам.
    add_world(mac, "Новый", {"a": b"1"})
    wait_for_rows(qtbot, mac_window, lambda rows: "Новый" in [r.folder_name for r in rows])


def test_window_notices_world_opened_in_game(devices: tuple[Device, Device], qtbot: QtBot,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("blocksync.ui.change_watcher.LOCK_POLL_MS", 200)
    _, mac = devices
    world = add_world(mac, "TEST", {"session.lock": b"x"})
    window = mac.open_window(qtbot)
    wait_for_rows(qtbot, window, lambda rows: len(rows) == 1 and rows[0].status is not WorldStatus.LOCKED)

    with hold_lock(world / "session.lock"):  # «зашли в мир в игре»
        wait_for_rows(qtbot, window, lambda rows: rows[0].status is WorldStatus.LOCKED)
    wait_for_rows(qtbot, window, lambda rows: rows[0].status is not WorldStatus.LOCKED)


def test_rows_sorted_by_last_sync_and_scroll_kept(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    pc, mac = devices
    for index in range(12):  # столько, чтобы появилась прокрутка
        add_world(mac, f"Мир {index:02}", {"a": b"1"}, mtime=BASE_TIME + index)
    add_record(mac.node.paths.history_file, SyncRecord(
        "2026-10-21T20:00:00", "Мир 03", Direction.RECEIVED, "ПК", folder="Мир 03", instance=mac.instance_id))
    add_record(mac.node.paths.history_file, SyncRecord(
        "2026-10-21T21:00:00", "Мир 07", Direction.SENT, "ПК", folder="Мир 07", instance=mac.instance_id))
    window = mac.open_window(qtbot)
    window.resize(1180, 500)
    window.show()
    # Сначала недавно синхронизированные, потом остальные — от свежих к старым.
    wait_for_rows(qtbot, window, lambda rows: [r.folder_name for r in rows][:3] == ["Мир 07", "Мир 03", "Мир 11"])

    bar = window.world_list.scroll_area.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0, timeout=TIMEOUT_MS)
    bar.setValue(bar.maximum() // 2)
    position = bar.value()
    window.refresh()
    qtbot.waitUntil(lambda: window._refresh_task is None, timeout=TIMEOUT_MS)
    qtbot.wait(100)
    assert bar.value() == position


def test_direction_is_suggested_from_last_sync(devices: tuple[Device, Device], qtbot: QtBot) -> None:
    """После синхронизации мир изменили на Маке — подсказка «играли здесь»; изменили и на ПК — конфликт."""
    pc, mac = devices
    add_world(pc, "TEST", {"a": b"1", "b": b"2"})
    pair_devices(pc, mac)
    window = mac.open_window(qtbot)
    wait_for_rows(qtbot, window, lambda rows: rows and rows[0].status is WorldStatus.ONLY_THERE)
    dialog = _run_pull(window, qtbot)
    dialog.accept()
    assert mac.node.sync_states.load(mac.instance_id, "TEST") is not None

    # Играли на Маке (часы при этом пусть показывают более раннее время — подсказка от них не зависит).
    write_file(mac.saves / "TEST" / "a", b"played on mac", mtime=BASE_TIME - 500)
    window.start_sync(window.world_list.row_for("TEST").row)
    dialog = window.sync_dialog
    assert dialog is not None
    qtbot.waitUntil(lambda: dialog.here_radio.isChecked(), timeout=TIMEOUT_MS)
    assert "изменился только на этом компьютере" in dialog.suggestion_label.text()
    # Если всё же выбрать «ПК», будет предупреждение, что менялся только Мак.
    dialog.peer_radio.setChecked(True)
    dialog.next_button.click()
    qtbot.waitUntil(lambda: dialog.preview is not None, timeout=TIMEOUT_MS)
    assert "менялся только на «Мак»" in dialog.warnings_label.text()
    dialog.reject()
    qtbot.waitUntil(lambda: window.sync_dialog is None, timeout=TIMEOUT_MS)

    # Теперь поиграли и на ПК: конфликт, ничего не отмечено.
    write_file(pc.saves / "TEST" / "b", b"played on pc", mtime=BASE_TIME + 900)
    window.start_sync(window.world_list.row_for("TEST").row)
    dialog = window.sync_dialog
    assert dialog is not None
    qtbot.waitUntil(lambda: "на обоих компьютерах" in dialog.suggestion_label.text(), timeout=TIMEOUT_MS)
    assert dialog.choice.checkedButton() is None
    dialog.here_radio.setChecked(True)
    dialog.next_button.click()
    qtbot.waitUntil(lambda: dialog.preview is not None, timeout=TIMEOUT_MS)
    assert "изменился и на «Мак», и на «ПК»" in dialog.warnings_label.text()
    assert not dialog.start_button.isEnabled()  # нужно подтверждение
    dialog.reject()


def _run_pull(window: MainWindow, qtbot: QtBot) -> SyncDialog:
    """Синхронизировать мир TEST «с другого компьютера» через окно и дождаться итога."""
    window.start_sync(window.world_list.row_for("TEST").row)
    dialog = window.sync_dialog
    assert dialog is not None
    dialog.peer_radio.setChecked(True)
    dialog.next_button.click()
    qtbot.waitUntil(lambda: dialog.start_button.isEnabled(), timeout=TIMEOUT_MS)
    dialog.start_button.click()
    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == 3, timeout=TIMEOUT_MS)
    assert "Готово" in dialog.result_label.text(), dialog.result_label.text()
    return dialog
