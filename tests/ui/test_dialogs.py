import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget

from e7auto.ui.pages.application import _UpdateResultDialog
from e7auto.ui.widgets import AppDialog
from e7auto.ui.styles import MAIN_WINDOW_STYLE
from e7auto.ui import MainWindow
from e7auto.ui.announcements import _AnnouncementDialog
from shiboken6 import delete


CASES = [
    ('无需更新', None, None),
    ('检查更新失败', '重试', None),
    ('发现新版本 v9.0.0', '更新', '# 更新内容\n\n## 新增\n- 示例'),
    ('发现新版本 v9.0.0', '更新', ''),
]


def open_dialog(qt_application, title, action, notes):
    parent = QWidget()
    parent.resize(1180, 760)
    parent.setStyleSheet(MAIN_WINDOW_STYLE)
    parent.show()
    dialog = _UpdateResultDialog(title, '说明', action, parent, notes=notes)
    dialog.open()
    qt_application.processEvents()
    return parent, dialog


def tab_to(dialog, target, *, backwards=False):
    for _ in range(10):
        if target.hasFocus():
            return
        QTest.keyClick(QApplication.focusWidget() or dialog, Qt.Key.Key_Tab,
                       Qt.KeyboardModifier.ShiftModifier if backwards else Qt.KeyboardModifier.NoModifier)
    assert target.hasFocus(), 'Control is unreachable by keyboard'


@pytest.mark.parametrize('title, action, notes', CASES)
def test_dialog_opens_on_container_and_initial_enter_does_not_act(qt_application, title, action, notes):
    parent, dialog = open_dialog(qt_application, title, action, notes)
    events = []
    dialog.accepted.connect(lambda: events.append('accepted'))
    dialog.rejected.connect(lambda: events.append('rejected'))
    dialog.update_requested.connect(lambda: events.append('update'))
    assert QApplication.focusWidget() is dialog
    assert all(not button.hasFocus() and not button.isDefault() and not button.autoDefault()
               for button in dialog.findChildren(QPushButton))
    if notes is not None:
        assert not dialog.notes_view.hasFocus()
        assert dialog.notes_view.isReadOnly()
        assert dialog.notes_view.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        QTest.keyClick(dialog, key)
    assert events == [] and dialog.isVisible()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert events == ['rejected'] and not dialog.isVisible()
    parent.close()


@pytest.mark.parametrize('title, action, notes', CASES)
@pytest.mark.parametrize('key', [Qt.Key.Key_Return, Qt.Key.Key_Space])
def test_tab_to_close_preserves_keyboard_activation(qt_application, title, action, notes, key):
    parent, dialog = open_dialog(qt_application, title, action, notes)
    closed = []
    dialog.rejected.connect(lambda: closed.append(True))
    tab_to(dialog, dialog.close_button)
    assert dialog.close_button.hasFocus() and not dialog.close_button.isDefault()
    QTest.keyClick(dialog.close_button, key)
    assert closed == [True] and not dialog.isVisible()
    parent.close()


@pytest.mark.parametrize('action, notes', [('重试', None), ('更新', '# 更新内容\n日志')])
@pytest.mark.parametrize('key', [Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space])
def test_shift_tab_to_action_activates_only_focused_action(qt_application, action, notes, key):
    parent, dialog = open_dialog(qt_application, '检查更新', action, notes)
    events = []
    dialog.accepted.connect(lambda: events.append('retry'))
    dialog.update_requested.connect(lambda: events.append('update'))
    tab_to(dialog, dialog.action_button, backwards=True)
    QTest.keyClick(dialog.action_button, key)
    assert events == ['retry' if action == '重试' else 'update']
    assert dialog.isVisible() is (action == '更新')
    dialog.close()
    parent.close()


def test_text_selection_reopen_and_busy_escape_guard_remain(qt_application):
    parent, dialog = open_dialog(qt_application, '发现新版本', '更新', '# 更新内容\n\n可以复制的日志')
    tab_to(dialog, dialog.notes_view)
    QTest.keyClick(dialog.notes_view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert '可以复制的日志' in dialog.notes_view.textCursor().selectedText()
    updates = []
    dialog.update_requested.connect(lambda: updates.append(True))
    QTest.keyClick(dialog.notes_view, Qt.Key.Key_Return)
    assert not updates and dialog.isVisible()
    dialog.hide()
    dialog.open()
    qt_application.processEvents()
    # The offscreen platform may keep the reopened window inactive; its saved
    # focus target must still be the container when it becomes active again.
    assert dialog.focusWidget() is dialog
    dialog.set_install_busy(True)
    dialog.set_install_launching(True)
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.isVisible() and not updates
    dialog.set_install_launching(False)
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    parent.close()


def test_future_dialog_subclasses_get_the_same_policy(qt_application):
    class FutureDialog(AppDialog):
        def __init__(self):
            super().__init__()
            self.input = QLineEdit(self)
            self.confirm = QPushButton('确认', self)
            # Even designer-created default buttons must not activate implicitly.
            self.confirm.setAutoDefault(True)
            self.confirm.setDefault(True)
            layout = QVBoxLayout(self)
            layout.addWidget(self.input)
            layout.addWidget(self.confirm)
    dialog = FutureDialog()
    clicks = []
    dialog.confirm.clicked.connect(lambda: clicks.append(True))
    dialog.open()
    qt_application.processEvents()
    assert QApplication.focusWidget() is dialog
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    assert not clicks
    tab_to(dialog, dialog.input)
    QTest.keyClick(dialog.input, Qt.Key.Key_Return)
    assert not clicks
    tab_to(dialog, dialog.confirm)
    QTest.keyClick(dialog.confirm, Qt.Key.Key_Return)
    assert clicks == [True]
    dialog.close()


def make_hosted_dialog(parent, kind):
    if kind == 'announcement':
        return _AnnouncementDialog(parent)
    if kind == 'future':
        dialog = AppDialog(parent)
        button = QPushButton('确认', dialog)
        button.clicked.connect(dialog.accept)
        QVBoxLayout(dialog).addWidget(button)
        return dialog
    title, action, notes = {
        'up_to_date': ('无需更新', None, None),
        'update_failed': ('检查更新失败', '重试', None),
        'update_available': ('发现新版本', '更新', '# 更新内容\n\n测试说明'),
    }[kind]
    return _UpdateResultDialog(title, '说明', action, parent, notes=notes)


@pytest.mark.parametrize('kind', ['announcement', 'up_to_date', 'update_failed', 'update_available', 'future'])
def test_shared_dialog_blocks_content_but_keeps_title_drag_available(tmp_path, qt_application, monkeypatch, kind):
    window = MainWindow(tmp_path)
    window.show()
    window._show_module_page('shop_refresh')
    page = window._pages.currentWidget()
    dialog = make_hosted_dialog(window, kind)
    dialog.open()
    qt_application.processEvents()
    body = window._dialog_backdrop.parentWidget()
    assert window.isEnabled() and not body.isEnabled()
    assert dialog.isEnabled() and not dialog.isModal()
    assert QApplication.activeModalWidget() is None
    assert window._title_bar.isEnabled() and not window._title_bar.back_button.isEnabled()
    assert all(button.isEnabled() for button in (window._title_bar._minimize,
                                               window._title_bar._maximize, window._title_bar._close))
    if QApplication.platformName() == 'windows':
        import ctypes
        assert ctypes.windll.user32.IsWindowEnabled(int(window.winId()))

    window._sidebar.buttons['about'].click()
    window._title_bar.back_button.click()
    window._show_section('about')
    window._show_module_page('chaos')
    window._launch_run('shop')
    assert window._pages.currentWidget() is page and window._preparation_thread is None
    moves = []
    monkeypatch.setattr(window.windowHandle(), 'startSystemMove', lambda: moves.append(True) or True)
    QTest.mousePress(window._title_bar, Qt.MouseButton.LeftButton, pos=QPoint(400, 20))
    QTest.mouseRelease(window._title_bar, Qt.MouseButton.LeftButton, pos=QPoint(400, 20))
    assert moves == [True]
    dialog.reject()
    assert body.isEnabled() and not window._dialog_backdrop.isVisible()
    window._show_section('about')
    assert window._pages.currentWidget() is window._about_page
    window.close()


@pytest.mark.parametrize('kind', ['announcement', 'up_to_date', 'update_failed', 'update_available', 'future'])
def test_shared_dialog_follows_window_move_resize_and_restore(tmp_path, qt_application, kind):
    window = MainWindow(tmp_path)
    window.resize(760, 560)
    window.show()
    dialog = make_hosted_dialog(window, kind)
    dialog.open()
    qt_application.processEvents()
    previous_window, previous_dialog = window.pos(), dialog.pos()
    window.move(window.pos() + QPoint(60, 40))
    qt_application.processEvents()
    assert ((dialog.pos() - previous_dialog) - (window.pos() - previous_window)).manhattanLength() <= 2
    for action in (lambda: window.resize(1100, 740), window.showMaximized, window.showNormal,
                   window.showMinimized, window.showNormal):
        action()
        qt_application.processEvents()
        assert window._dialog_host.active and not window._dialog_backdrop.parentWidget().isEnabled()
        if not window.isMinimized():
            assert dialog.isVisible()
            expected = window.mapToGlobal(window.rect().center())
            actual = dialog.frameGeometry().center()
            assert (actual - expected).manhattanLength() <= 2
    dialog.reject()
    window.close()


def test_shared_host_keeps_content_blocked_until_last_dialog_is_gone(tmp_path, qt_application):
    window = MainWindow(tmp_path)
    window.show()
    first = make_hosted_dialog(window, 'future')
    first.open()
    second = make_hosted_dialog(first, 'future')
    second.open()
    qt_application.processEvents()
    body = window._dialog_backdrop.parentWidget()
    second.reject()
    assert first.isVisible() and window._dialog_host.active and not body.isEnabled()
    first.hide()
    assert not window._dialog_host.active and body.isEnabled()
    first.open()
    assert window._dialog_host.active and not body.isEnabled()
    delete(first)
    assert not window._dialog_host.active and body.isEnabled()
    assert not window._dialog_backdrop.isVisible()
    window.close()


def test_shared_host_preserves_previously_disabled_content(tmp_path, qt_application):
    window = MainWindow(tmp_path)
    window.show()
    body = window._dialog_backdrop.parentWidget()
    body.setEnabled(False)
    dialog = make_hosted_dialog(window, 'future')
    dialog.open()
    dialog.reject()
    assert not body.isEnabled()
    window.close()


def test_title_bar_escape_and_parent_close_keep_dialog_rejection_guard(tmp_path, qt_application):
    class GuardedDialog(AppDialog):
        busy = True

        def reject(self):
            if not self.busy:
                super().reject()

    window = MainWindow(tmp_path)
    window.show()
    dialog = GuardedDialog(window)
    dialog.open()
    qt_application.processEvents()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert dialog.isVisible() and window._dialog_host.active
    assert not window.close()
    assert window.isVisible() and dialog.isVisible()
    dialog.busy = False
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert not dialog.isVisible() and not window._dialog_host.active and window.isVisible()
    dialog.open()
    assert window.close()
    assert not dialog.isVisible() and not window._dialog_host.active
