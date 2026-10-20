import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget

from e7auto.ui.pages.application import _UpdateResultDialog
from e7auto.ui.widgets import AppDialog
from e7auto.ui.styles import MAIN_WINDOW_STYLE


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
