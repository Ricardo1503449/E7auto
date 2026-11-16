"""Background preparation, signed disk recovery, and explicit installation."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
from threading import Event

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget

from e7auto.core.update_feed import VerifiedUpdateOffer, decode_feed
from e7auto.platform import update_install as native
from e7auto.ui.auto_updates import BackgroundUpdates
from e7auto.ui.main_window import MainWindow
from e7auto.ui.preferences import UpdatePreferences
from e7auto.ui.update_install import UpdateInstaller
from e7auto.ui.updates import UpdateController
from tests.helpers.qt import wait_for as wait_for_qt
from tests.helpers.update_install import configure_installed_update
from tests.helpers.updates import FeedReply, feed, verify_test_signature


DATA = b'MZ synthetic update package'


def wait_for(predicate):
    def poll():
        # qWait pumps Qt; yield Python too so the filesystem worker can progress.
        Event().wait(.001)
        return predicate()
    wait_for_qt(poll)


def offer_for(version='v9.0.0'):
    data = feed(version, sha256=hashlib.sha256(DATA).hexdigest(), size=len(DATA))
    return VerifiedUpdateOffer(decode_feed(data, verify_test_signature), data)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    configure_installed_update(tmp_path, monkeypatch)
    monkeypatch.setattr('e7auto.ui.auto_updates.is_installed_build', lambda: True)
    monkeypatch.setattr(native, 'verify_signature', verify_test_signature)
    def version_info(path, _key):
        version = tuple(map(int, re.search(r'_v(\d+)\.(\d+)\.(\d+)_', path).groups()))
        return {'FileVersionMS': version[0] << 16 | version[1], 'FileVersionLS': version[2] << 16}
    monkeypatch.setattr(native.win32api, 'GetFileVersionInfo', version_info)
    calls = []
    monkeypatch.setattr('e7auto.ui.update_install.launch_update', lambda *args: calls.append(args) or True)
    monkeypatch.setattr('e7auto.ui.update_install.download_urls', lambda asset: (asset.url,))
    created = []
    def make():
        installer = UpdateInstaller(tmp_path)
        created.append(installer)
        return installer
    yield tmp_path, monkeypatch, calls, make
    for installer in created:
        installer.cancel()
        wait_for(lambda: not installer.settling)


def download(installer, monkeypatch, *, data=DATA, status=200):
    reply = FeedReply(data, status)
    requests = []
    monkeypatch.setattr(installer._manager, 'get', lambda request: requests.append(request) or reply)
    return reply, requests


def prepare(installer, monkeypatch, offer=None):
    offer = offer or offer_for()
    reply, requests = download(installer, monkeypatch)
    installer.prepare(offer)
    reply.complete()
    wait_for(lambda: not installer.busy)
    assert installer.ready is not None
    return installer.ready, requests


def test_background_prepares_without_installing_then_explicitly_installs_once(environment):
    root, monkeypatch, calls, make = environment
    installer = make()
    ready, requests = prepare(installer, monkeypatch)
    assert not calls and not installer.blocking and len(requests) == 1
    assert ready.path.read_bytes() == DATA
    assert json.loads((root / 'state/updates/ready.json').read_text())['job_id'] == ready.path.parent.name
    installer.install_ready(ready.offer.asset)
    installer.install_ready(ready.offer.asset)
    wait_for(lambda: not installer.settling)
    assert len(calls) == 1 and calls[0][1] == ready.path


def test_disk_cache_survives_cancel_and_restores_offline_with_original_signed_notes(environment):
    root, monkeypatch, calls, make = environment
    first = make()
    ready, _ = prepare(first, monkeypatch)
    first.cancel()
    restored = make()
    monkeypatch.setattr(restored._manager, 'get', lambda request: pytest.fail('cached package must not download'))
    restored.restore()
    wait_for(lambda: not restored.busy)
    assert restored.ready == ready and not calls
    assert restored.ready.offer.release.notes == '## Changes'
    restored.install_ready(ready.offer.asset)
    wait_for(lambda: not restored.settling)
    assert len(calls) == 1


@pytest.mark.parametrize('damage', ['feed', 'digest', 'size', 'request', 'partial', 'pointer', 'pe', 'installed'])
def test_invalid_disk_cache_never_becomes_installable(environment, damage):
    root, monkeypatch, calls, make = environment
    ready, _ = prepare(make(), monkeypatch)
    if damage == 'feed':
        (ready.path.parent / 'feed.json').write_bytes(feed(sha256='b' * 64, size=len(DATA)))
    elif damage == 'digest':
        ready.path.write_bytes(b'XX' + DATA[2:])
    elif damage == 'size':
        ready.path.write_bytes(DATA[:-1])
    elif damage == 'request':
        (ready.path.parent / 'request.json').write_text('{}')
    elif damage == 'partial':
        ready.path.rename(ready.path.with_suffix('.exe.part'))
    elif damage == 'pointer':
        (root / 'state/updates/ready.json').write_text('{"schema_version":1,"job_id":"../outside"}')
    elif damage == 'pe':
        monkeypatch.setattr(native.win32api, 'GetFileVersionInfo', lambda *_: {'FileVersionMS': 8 << 16, 'FileVersionLS': 0})
    elif damage == 'installed':
        monkeypatch.setattr(native, '__version__', '9.0.0')
    restored = make()
    failures = []
    restored.background_failed.connect(failures.append)
    restored.restore()
    wait_for(lambda: not restored.busy)
    assert restored.ready is None and failures and not calls
    assert not (root / 'state/updates/ready.json').exists()


def test_cached_feed_signature_is_rechecked_and_legacy_requests_are_not_adopted(environment):
    root, monkeypatch, calls, make = environment
    ready, _ = prepare(make(), monkeypatch)
    def reject(*args):
        raise ValueError('bad signature')
    monkeypatch.setattr(native, 'verify_signature', reject)
    restored = make()
    restored.restore()
    wait_for(lambda: not restored.busy)
    assert restored.ready is None and not calls and ready.path.exists()
    legacy = make()
    legacy.restore()
    assert not legacy.busy and legacy.ready is None


def test_manual_request_joins_background_download_and_uac_failure_keeps_cache(environment):
    root, monkeypatch, calls, make = environment
    installer = make()
    reply, requests = download(installer, monkeypatch)
    offer = offer_for()
    installer.prepare(offer)
    assert installer.busy and not installer.blocking
    installer.start(offer.asset, offer=offer)
    assert installer.blocking and len(requests) == 1
    monkeypatch.setattr('e7auto.ui.update_install.launch_update', lambda *_: (_ for _ in ()).throw(OSError('UAC cancelled')))
    failures = []
    installer.failed.connect(failures.append)
    reply.complete()
    wait_for(lambda: not installer.busy)
    assert failures and installer.ready is not None and installer.ready.path.exists() and not calls
    monkeypatch.setattr('e7auto.ui.update_install.launch_update', lambda *args: calls.append(args) or True)
    installer.install_ready(offer.asset)
    wait_for(lambda: not installer.settling)
    assert len(calls) == 1 and len(requests) == 1


def test_cancel_stops_partial_and_late_reply_cannot_create_ready_or_install(environment):
    root, monkeypatch, calls, make = environment
    installer = make()
    reply, requests = download(installer, monkeypatch)
    installer.prepare(offer_for())
    installer.cancel()
    reply.readyRead.emit()
    reply.finished.emit()
    assert reply.aborted and not installer.busy and installer.ready is None and not calls
    assert not list(root.rglob('*.part'))


def test_default_off_opt_in_delay_shared_offer_and_disable_keep_full_package(environment):
    root, monkeypatch, calls, make = environment
    now = [1000.0]
    controller = UpdateController(root / 'state/update_status.json', clock=lambda: now[0])
    installer = make()
    background = BackgroundUpdates(root / 'state/ui_preferences.json', controller, installer, clock=lambda: now[0])
    requests = []
    monkeypatch.setattr(controller.checker, 'check', lambda: requests.append(True))
    background.start()
    assert not background.enabled and not background._timer.isActive()
    background.set_enabled(True)
    background.set_enabled(True)
    assert requests == [True] and background._timer.interval() == 60000
    offer = offer_for()
    controller.checker.offer, controller.checker.asset = offer, offer.asset
    controller._completed(offer.asset.version, True, offer.release.notes)
    background._timer.stop()
    background._prepare()
    assert not installer.busy
    reply, downloads = download(installer, monkeypatch)
    now[0] += 60
    background._prepare()
    assert len(downloads) == 1 and requests == [True]
    reply.complete()
    wait_for(lambda: not installer.busy)
    ready = installer.ready
    assert background.should_prompt and not calls
    background.set_enabled(False)
    assert ready.path.exists() and not background.should_prompt
    assert UpdatePreferences(root / 'state/ui_preferences.json').load() is False
    background.cancel()
    controller.cancel()


def test_auto_failure_waits_before_retry_and_later_is_persisted_per_version(environment):
    root, monkeypatch, calls, make = environment
    now = [1000.0]
    controller = UpdateController(root / 'state/update_status.json', clock=lambda: now[0])
    installer = make()
    background = BackgroundUpdates(root / 'state/ui_preferences.json', controller, installer, clock=lambda: now[0])
    controller.offer = offer_for()
    controller._fresh_result = True
    background.start()
    background.set_enabled(True)
    now[0] += 60
    bad, requests = download(installer, monkeypatch, status=503)
    background._prepare()
    bad.complete()
    assert background._retry_at == now[0] + 1800 and not calls
    background._prepare()
    assert len(requests) == 1
    now[0] += 1800
    good, requests = download(installer, monkeypatch)
    background._prepare()
    good.complete()
    wait_for(lambda: not installer.busy)
    background.dismiss()
    assert not background.should_prompt
    restored = BackgroundUpdates(root / 'state/ui_preferences.json', controller, installer)
    assert not restored.should_prompt
    newer = replace(installer.ready, offer=offer_for('v10.0.0'))
    installer.ready = newer
    assert restored.should_prompt
    background.cancel()
    restored.cancel()
    controller.cancel()


def test_toggle_write_failure_reverts_and_does_not_touch_other_preferences(environment):
    root, monkeypatch, calls, make = environment
    path = root / 'state/ui_preferences.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text('{"run_end_notifications":false}')
    controller = UpdateController(root / 'state/update_status.json')
    background = BackgroundUpdates(path, controller, make())
    errors, enabled = [], []
    background.status_changed.connect(errors.append)
    background.enabled_changed.connect(enabled.append)
    monkeypatch.setattr(background.preferences, 'save', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    background.set_enabled(True)
    assert errors and enabled == [False] and not background.enabled and not calls
    assert json.loads(path.read_text()) == {'run_end_notifications': False}
    background.cancel()
    controller.cancel()


@pytest.mark.parametrize('size', [QSize(760, 560), QSize(1180, 760)])
def test_ready_card_waits_for_thread_exit_and_later_preserves_manual_entry(environment, size):
    root, monkeypatch, calls, make = environment
    ready, _ = prepare(make(), monkeypatch)
    window = MainWindow(root)
    window.resize(size)
    window._update_controller._fresh_result = True
    window.show()
    wait_for(lambda: window._update_installer.ready is not None)
    window._background_updates.set_enabled(True)
    class Running:
        exited = False
        def wait(self, _): return self.exited
        def deleteLater(self): pass
    thread = Running()
    window._thread = thread
    window._entered_inventory = True
    window._sync_update_ui()
    assert not window._update_ready_card.isVisible()
    window._on_thread_stopped()
    assert window._thread is thread
    thread.exited = True
    window._on_thread_stopped()
    card = window._update_ready_card
    assert card.isVisible() and window._thread is None and not calls
    assert card.geometry().right() < card.parentWidget().width()
    assert card.geometry().bottom() < card.parentWidget().height()
    assert all(not widget.toolTip() for widget in [card, *card.findChildren(QWidget)])
    assert [label.text() for label in window._settings_page.findChild(QWidget, 'autoDownloadUpdatesRow').findChildren(QLabel)] == ['自动下载更新']
    card.later_button.click()
    assert not card.isVisible() and window._settings_page.install_downloaded_button.isHidden() is False
    assert window._sidebar.buttons['settings']._update_available
    window.close()


def test_background_download_allows_task_start_and_close_cancels_transfer(environment):
    root, monkeypatch, calls, make = environment
    window = MainWindow(root)
    window.show()
    reply, requests = download(window._update_installer, monkeypatch)
    window._update_installer.prepare(offer_for())
    assert not window._is_busy() and window._shop_feature_page.start_button.isEnabled()
    monkeypatch.setattr('e7auto.ui.main_window.PreparationThread.start', lambda self: None)
    window._launch_run('shop')
    assert window._preparation_thread is not None
    window._preparation_thread.cancel()
    window._on_prepared()
    window.close()
    reply.finished.emit()
    assert reply.aborted and not calls and not window.isVisible()


def test_close_waits_for_cache_worker_and_preserves_verified_package(environment):
    root, monkeypatch, calls, make = environment
    ready, _ = prepare(make(), monkeypatch)
    import e7auto.ui.update_install as update_ui
    original = update_ui.load_prepared_update
    entered, proceed = Event(), Event()
    def blocked_load(root, cancelled):
        entered.set()
        assert proceed.wait(2)
        return original(root, cancelled)
    monkeypatch.setattr(update_ui, 'load_prepared_update', blocked_load)
    window = MainWindow(root)
    window.show()
    wait_for(entered.is_set)
    window.close()
    assert window.isVisible() and window._close_requested and window._update_installer.settling
    proceed.set()
    wait_for(lambda: not window.isVisible())
    assert ready.path.exists() and (root / 'state/updates/ready.json').exists() and not calls


@pytest.mark.parametrize('entry', ['card', 'settings'])
def test_ui_installs_cached_version_only_after_explicit_action(environment, entry):
    root, monkeypatch, calls, make = environment
    ready, _ = prepare(make(), monkeypatch)
    window = MainWindow(root)
    window._update_controller._fresh_result = True
    window.show()
    wait_for(lambda: window._update_installer.ready is not None)
    monkeypatch.setattr(window._update_installer._manager, 'get', lambda _: pytest.fail('must use cache'))
    assert not calls
    if entry == 'card':
        window._background_updates.set_enabled(True)
        window._update_ready_card.install_button.setFocus()
        QTest.keyClick(window._update_ready_card.install_button, Qt.Key.Key_Space)
    else:
        window._settings_page._install_downloaded()
    wait_for(lambda: not window.isVisible())
    assert len(calls) == 1 and calls[0][1] == ready.path


@pytest.mark.parametrize('admission', ['busy', 'downloading'])
def test_cancel_admission_does_not_start_worker_or_automatically_retry(environment, admission):
    root, monkeypatch, calls, make = environment
    installer = make()
    monkeypatch.setattr(installer._manager, 'get', lambda _: pytest.fail('cancel must win'))
    if admission == 'busy':
        installer.busy_changed.connect(lambda busy: installer.cancel() if busy else None)
    else:
        installer.state_changed.connect(lambda state: installer.cancel() if state == 'downloading' else None)
    installer.prepare(offer_for())
    assert not installer.busy and not installer.settling and not list(root.rglob('*.part'))
    assert not installer._deadline.isActive()
    if admission == 'busy':
        assert not (root / 'state/updates').exists()
    controller = UpdateController(root / 'state/update_status.json')
    controller.offer, controller._fresh_result = offer_for(), True
    background = BackgroundUpdates(root / 'state/ui_preferences.json', controller, installer)
    background.start()
    background.set_enabled(True)
    background._timer.stop()
    background._first_download_at = 0
    background._prepare()
    assert not background._timer.isActive() and not calls
    background.cancel()
    controller.cancel()


def test_manual_request_during_cache_restore_blocks_new_tasks_until_handoff(environment):
    root, monkeypatch, calls, make = environment
    ready, _ = prepare(make(), monkeypatch)
    import e7auto.ui.update_install as update_ui
    original = update_ui.load_prepared_update
    entered, proceed = Event(), Event()
    def blocked_load(root, cancelled):
        entered.set()
        assert proceed.wait(2)
        return original(root, cancelled)
    monkeypatch.setattr(update_ui, 'load_prepared_update', blocked_load)
    installer = make()
    installer.restore()
    wait_for(entered.is_set)
    reply, _ = download(installer, monkeypatch)
    installer.start(ready.offer.asset, offer=ready.offer)
    assert installer.blocking
    proceed.set()
    wait_for(lambda: installer.state == 'downloading')
    reply.complete()
    wait_for(lambda: not installer.busy)
    assert len(calls) == 1


def test_source_toggle_is_disabled_and_ready_install_is_refused_during_preparation(environment):
    root, monkeypatch, calls, make = environment
    monkeypatch.setattr('e7auto.ui.auto_updates.is_installed_build', lambda: False)
    window = MainWindow(root)
    window.show()
    assert not window._settings_page.auto_updates_toggle.isEnabled()
    assert '源码' in window._settings_page.status_label.text()
    ready, _ = prepare(make(), monkeypatch)
    window._update_installer.ready = ready
    window._preparation_thread = object()
    window._install_prepared_update(ready.offer.asset)
    assert not calls and '请先结束' in window._settings_page.status_label.text()
    window._preparation_thread = None
    window.close()


def test_new_verified_offer_replaces_inflight_background_target(environment):
    root, monkeypatch, calls, make = environment
    now = [1000.0]
    controller = UpdateController(root / 'state/update_status.json', clock=lambda: now[0])
    installer = make()
    background = BackgroundUpdates(root / 'state/ui_preferences.json', controller, installer, clock=lambda: now[0])
    controller.offer, controller._fresh_result = offer_for(), True
    background.start()
    background.set_enabled(True)
    now[0] += 60
    first, requests = download(installer, monkeypatch)
    background._prepare()
    newer = offer_for('v10.0.0')
    controller.offer = newer
    controller.offer_changed.emit(newer)
    second, replacement_requests = download(installer, monkeypatch)
    background._prepare()
    assert first.aborted and len(requests) == len(replacement_requests) == 1
    first.finished.emit()
    second.complete()
    wait_for(lambda: not installer.busy)
    assert installer.ready.offer == newer and background.should_prompt and not calls
    background.cancel()
    controller.cancel()


def test_manual_failure_uses_background_backoff_instead_of_immediate_retry(environment):
    root, monkeypatch, calls, make = environment
    now = [1000.0]
    controller = UpdateController(root / 'state/update_status.json', clock=lambda: now[0])
    installer = make()
    background = BackgroundUpdates(root / 'state/ui_preferences.json', controller, installer, clock=lambda: now[0])
    controller.offer, controller._fresh_result = offer_for(), True
    background.start()
    background.set_enabled(True)
    now[0] += 60
    bad, requests = download(installer, monkeypatch, status=503)
    installer.start(controller.offer.asset, offer=controller.offer)
    bad.complete()
    assert background._retry_at == now[0] + 1800
    background._prepare()
    assert len(requests) == 1 and not calls
    background.cancel()
    controller.cancel()


def test_cancellation_at_verification_boundary_does_not_start_a_worker(environment):
    root, monkeypatch, calls, make = environment
    installer = make()
    reply, _ = download(installer, monkeypatch)
    installer.state_changed.connect(lambda state: installer.cancel() if state == 'verifying' else None)
    installer.prepare(offer_for())
    reply.complete()
    assert not installer.busy and not installer.settling and installer.ready is None and not calls
    assert not (root / 'state/updates/ready.json').exists()


def test_reverified_newer_cache_cannot_be_replaced_by_an_older_feed(environment):
    root, monkeypatch, calls, make = environment
    ready, _ = prepare(make(), monkeypatch, offer_for('v10.0.0'))
    controller = UpdateController(root / 'state/update_status.json')
    controller.latest_version = 'v9.0.0'
    controller.offer = offer_for()
    installer = make()
    background = BackgroundUpdates(root / 'state/ui_preferences.json', controller, installer)
    installer.restore()
    wait_for(lambda: not installer.busy)
    assert installer.ready == ready and controller.latest_version == 'v10.0.0'
    controller._checking = True
    controller.checker.offer, controller.checker.asset = offer_for(), offer_for().asset
    controller._completed('v9.0.0', True, 'older source')
    assert installer.ready == ready and controller.latest_version == 'v10.0.0' and not calls
    background.cancel()
    controller.cancel()


def test_manual_target_changed_before_ready_requires_a_new_confirmation(environment):
    root, monkeypatch, calls, make = environment
    controller = UpdateController(root / 'state/update_status.json')
    installer = make()
    background = BackgroundUpdates(root / 'state/ui_preferences.json', controller, installer)
    old = offer_for()
    controller.latest_version, controller.offer = old.asset.version, old
    reply, _ = download(installer, monkeypatch)
    installer.start(old.asset, offer=old)
    newer = offer_for('v10.0.0')
    controller.latest_version, controller.offer = newer.asset.version, newer
    controller.offer_changed.emit(newer)
    failures = []
    installer.failed.connect(failures.append)
    reply.complete()
    wait_for(lambda: not installer.busy)
    assert not calls and installer.ready is None and failures
    assert not (root / 'state/updates/ready.json').exists()
    background.cancel()
    controller.cancel()


def test_invalid_installation_identity_preserves_cache_pointer_and_never_installs(environment):
    root, monkeypatch, calls, make = environment
    ready, _ = prepare(make(), monkeypatch)
    pointer = root / 'state/updates/ready.json'
    original = pointer.read_bytes()
    monkeypatch.setattr(native.win32api, 'GetModuleFileName', lambda _: str(root / 'Other.exe'))
    installer = make()
    installer.restore()
    wait_for(lambda: not installer.busy)
    assert installer.ready is None and not calls
    assert ready.path.exists() and pointer.read_bytes() == original
