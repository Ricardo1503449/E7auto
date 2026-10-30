from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QWidget

from e7auto import __version__
from e7auto.ui import MainWindow
from e7auto.ui.styles import COLORS
from e7auto.ui.updates import PROJECT_URL, UpdateController
from tests.helpers.qt import wait_for


def _fail_open(target):
    raise OSError("simulated default-handler failure")


def test_sidebar_default_toggle_selection_and_shared_back(tmp_path, qt_application):
    window = MainWindow(tmp_path)
    window.show()
    qt_application.processEvents()
    sidebar = window._sidebar
    assert not sidebar.expanded
    assert all(button.text() == "" for button in sidebar.buttons.values())
    assert sidebar.buttons["home"].isChecked()
    assert window._pages.currentWidget() is window._function_center_page
    assert len(window._function_center_page.cards) == 4
    assert len(window.findChildren(QPushButton, "backToModulesButton")) == 1
    back = window._title_bar.back_button
    assert back.text() == "" and back.accessibleName() == "返回功能中心"
    title = window.findChild(QLabel, "windowTitle")
    icon = window.findChild(QLabel, "titleBarIcon")
    version = window.findChild(QLabel, "titleBarVersion")
    assert back.geometry().right() < icon.geometry().left() < title.geometry().left()
    assert version.geometry().left() > title.geometry().right()
    assert version.text() == window._settings_page.version_label.text() == f"v{__version__}"
    version_label = window._about_page.findChild(QLabel, "aboutVersion")
    description = window._about_page.findChild(QLabel, "aboutDescription")
    assert version_label.text() == f"v{__version__}"
    assert version_label.font().pixelSize() == description.font().pixelSize()
    assert version.font().pixelSize() == 14
    assert window._title_bar.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert not sidebar.toggle_button.isCheckable()
    assert not back.isCheckable()
    assert all(not widget.toolTip() for widget in window.findChildren(QWidget))

    sidebar.toggle_button.setFocus()
    QTest.keyClick(sidebar.toggle_button, Qt.Key.Key_Space)
    assert sidebar.expanded
    assert [button.text() for button in sidebar.buttons.values()] == ["功能中心", "关于", "设置"]
    for section, page in (("about", window._about_page), ("settings", window._settings_page),
                          ("home", window._function_center_page)):
        sidebar.buttons[section].click()
        assert window._pages.currentWidget() is page
        assert [key for key, button in sidebar.buttons.items() if button.isChecked()] == [section]
        sidebar.buttons[section].click()
        assert sidebar.buttons[section].isChecked()
    for expanded in (True, False):
        sidebar.set_expanded(expanded)
        assert sidebar.toggle_button.toolTip() == ""
        for module, page in window._module_pages.items():
            window._show_module_page(module)
            assert window._pages.currentWidget() is page
            assert sidebar.buttons["home"].isChecked()
            back.click()
            assert window._pages.currentWidget() is window._function_center_page
            assert sidebar.expanded is expanded
        for section in ("settings", "about"):
            sidebar.buttons[section].click()
            back.click()
            assert window._pages.currentWidget() is window._function_center_page
            assert sidebar.expanded is expanded


def test_navigation_cannot_leave_a_running_feature(tmp_path):
    window = MainWindow(tmp_path)
    window._show_module_page("shop_refresh")
    window._thread = object()
    try:
        for section in ("settings", "about", "home"):
            window._sidebar.buttons[section].click()
            assert window._pages.currentWidget() is window._shop_feature_page
            assert window._sidebar.buttons["home"].isChecked()
        window._title_bar.back_button.click()
        assert window._pages.currentWidget() is window._shop_feature_page
    finally:
        window._thread = None


@pytest.mark.parametrize("expanded", [False, True])
def test_all_pages_fit_minimum_window_with_accessible_actions(tmp_path, qt_application, expanded):
    window = MainWindow(tmp_path)
    window._sidebar.set_expanded(expanded)
    window.resize(window.minimumSize())
    window.show()
    for page in (window._function_center_page, *window._module_pages.values(), window._settings_page, window._about_page):
        window._pages.setCurrentWidget(page)
        qt_application.processEvents()
        assert window.width() == window.minimumWidth()
        scroll = page.findChild(QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0
        assert page.findChild(QLabel, "pageHeading") is None
        if page is not window._function_center_page:
            heading = page.findChild(QLabel, "applicationHeading")
            assert heading.parentWidget().objectName() == (
                "settingsCard" if page in window._module_pages.values() else "applicationCard"
            )
            assert heading.font().pixelSize() == 24
        if hasattr(page, "start_button"):
            for control_name in ("limit_input", "stage_input", "lineup_input"):
                control = getattr(page, control_name, None)
                if control is not None:
                    assert control.parentWidget().rect().contains(control.geometry())
            scroll.ensureWidgetVisible(page.start_button)
            qt_application.processEvents()
            center = page.start_button.mapTo(scroll.viewport(), page.start_button.rect().center())
            assert scroll.viewport().rect().contains(center)
    assert window._sidebar.buttons["about"].geometry().top() > window._sidebar.buttons["home"].geometry().bottom()
    for button, title in ((window._settings_page.logs_button, "日志目录"),
                          (window._settings_page.github_button, "GitHub 项目地址"),
                          (window._settings_page.update_button, "检查更新")):
        assert [label.text() for label in button.findChildren(QLabel)] == [title]


def test_settings_opens_runtime_logs_and_project_and_reports_failure(tmp_path, monkeypatch):
    window = MainWindow(tmp_path / "安装 路径")
    page = window._settings_page
    opened = []
    monkeypatch.setattr("e7auto.ui.desktop_actions.open_default", lambda url: opened.append(url) or True)
    page.logs_button.click()
    page.logs_button.click()
    assert not page.logs_button.isEnabled() and page.logs_button._spinner_timer.isActive()
    assert not opened
    wait_for(page.logs_button.isEnabled)
    assert len(opened) == 1
    assert page.logs_button.isEnabled() and not page.logs_button._spinner_timer.isActive()
    assert Path(opened[-1]) == (tmp_path / "安装 路径" / "logs")
    assert Path(opened[-1]).is_dir()
    page.github_button.click()
    assert not page.github_button.isEnabled() and page.github_button._spinner_timer.isActive()
    wait_for(page.github_button.isEnabled)
    assert page.github_button.isEnabled() and not page.github_button._spinner_timer.isActive()
    assert opened[-1] == PROJECT_URL
    monkeypatch.setattr("e7auto.ui.desktop_actions.open_default", _fail_open)
    page.logs_button.click()
    wait_for(page.logs_button.isEnabled)
    assert "无法打开" in page.status_label.text()
    assert page.logs_button.isEnabled() and not page.logs_button._spinner_timer.isActive()
    page.github_button.click()
    wait_for(page.github_button.isEnabled)
    assert "无法打开" in page.status_label.text()
    assert page.github_button.isEnabled() and not page.github_button._spinner_timer.isActive()


def test_log_creation_failure_does_not_open_another_folder(tmp_path, monkeypatch):
    (tmp_path / "logs").write_text("not a directory")
    opened = []
    monkeypatch.setattr("e7auto.ui.desktop_actions.open_default", lambda url: opened.append(url))
    window = MainWindow(tmp_path)
    window._settings_page.logs_button.click()
    wait_for(window._settings_page.logs_button.isEnabled)
    assert not opened
    assert "目录权限" in window._settings_page.status_label.text()
    assert window._settings_page.logs_button.isEnabled()
    assert not window._settings_page.logs_button._spinner_timer.isActive()


def test_check_updates_feedback_retry_and_release_notes(tmp_path, monkeypatch, qt_application):
    window = MainWindow(tmp_path)
    page = window._settings_page
    window._show_section("settings")
    window.show()
    qt_application.processEvents()
    requests, opened = [], []
    monkeypatch.setattr(page.update_checker, "check", lambda: requests.append(True))
    monkeypatch.setattr("e7auto.ui.desktop_actions.open_default", lambda url: opened.append(url) or True)
    assert requests == []  # Opening Settings does not make an immediate request.
    page.update_button.click()
    page.update_button.click()
    assert requests == []
    assert not page.update_button.isEnabled()
    assert page.update_button._spinner_timer.isActive()
    angle = page.update_button._spinner_angle
    wait_for(lambda: requests == [True] and page.update_button._spinner_angle != angle)
    assert requests == [True]
    assert page.update_button._spinner_angle != angle
    assert page.status_label.isHidden()
    page.update_checker.failed.emit("网络失败")
    assert not page.update_button._busy and not page.update_button.isEnabled()
    assert not page.update_button._spinner_timer.isActive()
    dialog = page._update_dialog
    assert dialog.isVisible() and not dialog.isModal()
    assert window._dialog_backdrop.isVisible()
    assert dialog.heading.text() == "检查更新失败" and dialog.message.text() == "网络失败"
    dialog.action_button.click()
    assert window._dialog_backdrop.isHidden()
    assert page.update_button._spinner_timer.isActive()
    page.update_checker.completed.emit(f"v{__version__}", False, "")
    assert page._update_dialog.heading.text() == "无需更新"
    assert window._dialog_backdrop.isVisible()
    assert not page.update_button._spinner_timer.isActive()
    QTest.keyClick(page._update_dialog, Qt.Key.Key_Escape)
    assert page._update_dialog is None
    assert window._dialog_backdrop.isHidden()
    page.update_button.click()
    wait_for(lambda: len(requests) == 3)
    page.update_checker.completed.emit("v9.0.0", True, "## 改进\n\n- 新增侧边栏\n- 调整标题栏\n")
    assert opened == []
    assert page._update_dialog.heading.text() == "发现新版本 v9.0.0"
    assert window._dialog_backdrop.isVisible()
    assert "新增侧边栏" in page._update_dialog.notes_view.toPlainText()
    assert [button.text() for button in page._update_dialog.findChildren(QPushButton)] == ["关闭", "更新"]
    page._update_dialog.close_button.click()
    assert window._dialog_backdrop.isHidden()
    assert opened == []
    assert requests == [True, True, True]
    assert page._update_dialog is None


@pytest.mark.parametrize("completed", [False, True])
def test_window_close_cancels_update_animation_and_result_dialog(tmp_path, monkeypatch, completed):
    window = MainWindow(tmp_path)
    page = window._settings_page
    cancellations = []
    monkeypatch.setattr(page.update_checker, "check", lambda: None)
    monkeypatch.setattr(page.update_checker, "cancel", lambda: cancellations.append(True))
    page.update_button.click()
    if completed:
        wait_for(lambda: page._update_controller.checking)
        page.update_checker.completed.emit(f"v{__version__}", False, "")
        assert page._update_dialog is not None
    window.close()
    assert cancellations == [True]
    assert window._dialog_backdrop.isHidden()
    assert not page.update_button._spinner_timer.isActive()
    assert page.update_button.isEnabled()
    assert page._update_dialog is None


@pytest.mark.parametrize("name", ["logs_button", "github_button", "update_button"])
def test_closing_window_cancels_queued_settings_actions(tmp_path, monkeypatch, name):
    window = MainWindow(tmp_path)
    page = window._settings_page
    actions = []
    monkeypatch.setattr(page.update_checker, "check", lambda: actions.append("update"))
    monkeypatch.setattr("e7auto.ui.desktop_actions.open_default", lambda url: actions.append(url) or True)
    button = getattr(page, name)
    button.click()
    assert button._busy
    window.close()
    QTest.qWait(120)
    assert actions == []
    assert button.isEnabled() and not button._spinner_timer.isActive()
    assert not button._action_timer.isActive()


@pytest.mark.parametrize("notes", ["", "## 改动日志\n" + "- 更新项目\n" * 100])
def test_release_notes_empty_fallback_and_long_content_scroll(tmp_path, qt_application, monkeypatch, notes):
    window = MainWindow(tmp_path)
    window.resize(window.minimumSize())
    window.show()
    page = window._settings_page
    monkeypatch.setattr(page.update_checker, "check", lambda: None)
    page._update_controller.check(manual=True)
    page.update_checker.completed.emit("v9.0.0", True, notes)
    qt_application.processEvents()
    dialog = page._update_dialog
    view = dialog.notes_view
    backdrop = window._dialog_backdrop
    assert backdrop.isVisible()
    assert backdrop.geometry() == backdrop.parentWidget().rect()
    assert backdrop.palette().color(QPalette.ColorRole.Window).getRgb() == (0, 0, 0, 128)
    assert not backdrop.isAncestorOf(dialog)
    assert dialog.width() < window.width() and dialog.height() < window.height()
    assert not view.openLinks() and not view.openExternalLinks()
    assert view.horizontalScrollBar().maximum() == 0
    if notes:
        assert view.verticalScrollBar().maximum() > 0
    else:
        assert view.toPlainText() == "此版本未提供改动日志。"
    window.resize(1000, 700)
    qt_application.processEvents()
    assert backdrop.geometry() == backdrop.parentWidget().rect()
    dialog.close()
    assert backdrop.isHidden()


def test_first_show_checks_after_one_second_and_does_not_restart_on_reshow(
        tmp_path, qt_application, monkeypatch, offline_update_startup):
    monkeypatch.setattr(UpdateController, "start", offline_update_startup)
    window = MainWindow(tmp_path)
    controller = window._update_controller
    controller._clock = lambda: 100_000.0
    requests = []
    monkeypatch.setattr(controller.checker, "check", lambda: requests.append(True))
    assert not controller._auto_timer.isActive()
    window.show()
    qt_application.processEvents()
    assert not requests and controller._auto_timer.interval() == 1000
    wait_for(lambda: requests == [True], timeout_ms=2000)
    assert window._settings_page._update_dialog is None
    controller.checker.completed.emit("v9.0.0", True, "日志")
    window.hide()
    window.show()
    qt_application.processEvents()
    assert controller._auto_timer.interval() == 6 * 60 * 60 * 1000
    assert requests == [True]
    window.close()
    assert not controller._auto_timer.isActive()


def test_background_badges_are_shared_persist_after_reading_and_clear_on_current_version(
        tmp_path, qt_application, monkeypatch):
    window = MainWindow(tmp_path)
    window.show()
    page, controller = window._settings_page, window._update_controller
    navigation = window._sidebar.buttons["settings"]
    monkeypatch.setattr(controller.checker, "check", lambda: None)
    controller.check()
    controller.checker.completed.emit("v9.0.0", True, "日志")
    assert navigation._update_available and page.update_button._update_available
    assert page._update_dialog is None and not page.update_button._busy
    window._show_section("settings")
    assert navigation._update_available and page.update_button._update_available
    page.update_button.click()
    wait_for(lambda: controller._checking)
    assert "有新版本可用" in page.update_button.accessibleDescription()
    controller.checker.completed.emit("v9.0.0", True, "日志")
    page._update_dialog.close_button.click()
    assert navigation._update_available and page.update_button._update_available
    controller.check()
    controller.checker.failed.emit("离线")
    assert navigation._update_available and page.update_button._update_available
    assert page._update_dialog is None
    controller.check()
    controller.checker.completed.emit(f"v{__version__}", False, "")
    assert not navigation._update_available and not page.update_button._update_available
    assert navigation.accessibleDescription() == page.update_button.accessibleDescription() == ""
    assert all(not widget.toolTip() for widget in window.findChildren(QWidget))


@pytest.mark.parametrize("succeeds", [False, True])
def test_manual_click_joins_background_before_row_delay_and_does_not_repeat_request(
        tmp_path, qt_application, monkeypatch, succeeds):
    window = MainWindow(tmp_path)
    window._show_section("settings")
    window.show()
    page, controller = window._settings_page, window._update_controller
    requests = []
    monkeypatch.setattr(controller.checker, "check", lambda: requests.append(True))
    controller.check()
    page.update_button.click()
    assert controller._manual and not page.update_button._action_timer.isActive()
    if succeeds:
        controller.checker.completed.emit("v9.0.0", True, "日志")
        assert page._update_dialog.heading.text() == "发现新版本 v9.0.0"
    else:
        controller.checker.failed.emit("离线")
        assert page._update_dialog.heading.text() == "检查更新失败"
    QTest.qWait(120)
    assert requests == [True] and not page.update_button._busy
    assert not page.update_button._spinner_timer.isActive()


@pytest.mark.parametrize("expanded, scale", [(False, 1), (True, 1.5), (False, 2)])
def test_badges_render_at_title_and_gear_upper_right_and_stay_when_busy(
        tmp_path, qt_application, monkeypatch, expanded, scale):
    window = MainWindow(tmp_path)
    window._sidebar.set_expanded(expanded)
    window._show_section("settings")
    window.show()
    qt_application.processEvents()
    page, controller = window._settings_page, window._update_controller
    monkeypatch.setattr(controller.checker, "check", lambda: None)
    controller.check()
    controller.checker.completed.emit("v9.0.0", True, "")
    dot_color = QColor(COLORS["update_dot"])

    def red_bounds(widget):
        image = QImage(widget.size() * scale, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(scale)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        points = [(x / scale, y / scale) for y in range(image.height()) for x in range(image.width())
                  if image.pixelColor(x, y).rgb() == dot_color.rgb()]
        assert points
        return min(x for x, y in points), min(y for x, y in points), max(x for x, y in points), max(y for x, y in points)

    row, label = page.update_button, page.update_button._title_label
    first_bounds = None
    for width in (760, 1180):
        window.resize(width, 760)
        qt_application.processEvents()
        bounds = red_bounds(row)
        text = label.fontMetrics().boundingRect(label.contentsRect(), int(label.alignment()), label.text())
        assert label.x() + text.right() + 1 <= bounds[0] <= label.x() + text.right() + 4
        assert abs(bounds[1] - (label.y() + text.top())) <= 2
        assert bounds[2] < row.width() - 52
        if first_bounds is not None:
            assert bounds == first_bounds
        first_bounds = bounds
    row.set_busy(True)
    assert red_bounds(row) == first_bounds
    row.set_busy(False)
    navigation = window._sidebar.buttons["settings"]
    bounds = red_bounds(navigation)
    assert 30 <= bounds[0] <= 33 and 5 <= bounds[1] <= 8
    window._sidebar.set_navigation_enabled(False)
    assert red_bounds(navigation) == bounds
    window._sidebar.set_expanded(not expanded)
    qt_application.processEvents()
    assert red_bounds(navigation) == bounds
    assert label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert row.toolTip() == navigation.toolTip() == ""



@pytest.mark.parametrize('source_tree', [False, True])
def test_about_usage_guide_reuses_row_and_opens_existing_markdown(tmp_path, monkeypatch, qt_application, source_tree):
    root = tmp_path / '安装 路径'
    guide = root / 'docs/user/使用说明.md' if source_tree else root / '使用说明.md'
    guide.parent.mkdir(parents=True)
    guide.write_text('# 使用说明\n\n测试内容。\n', encoding='utf-8')
    window = MainWindow(root)
    window.show()
    window._sidebar.buttons['about'].click()
    qt_application.processEvents()
    page, opened = window._about_page, []
    button = page.guide_button
    assert type(button) is type(window._settings_page.logs_button)
    assert button.accessibleName() == '使用说明' and button.toolTip() == ''
    assert [label.text() for label in button.findChildren(QLabel)] == ['使用说明']
    assert button.parentWidget() is page.heading.parentWidget()
    monkeypatch.setattr('e7auto.ui.desktop_actions.open_default', lambda target: opened.append(target))
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    button.click()
    assert not button.isEnabled() and button._spinner_timer.isActive()
    wait_for(button.isEnabled)
    assert opened == [str(guide.resolve())] and not button._spinner_timer.isActive()
    assert guide.is_file() and '测试内容。' in guide.read_text(encoding='utf-8')
    scroll = page.findChild(QScrollArea)
    scroll.ensureWidgetVisible(button)
    qt_application.processEvents()
    center = button.mapTo(scroll.viewport(), button.rect().center())
    assert scroll.viewport().rect().contains(center)
    window.close()


@pytest.mark.parametrize('missing', [False, True])
def test_about_guide_missing_file_or_handler_error_restores_row(tmp_path, monkeypatch, missing):
    guide = tmp_path / '使用说明.md'
    if not missing:
        guide.write_text('# 使用说明\n', encoding='utf-8')
    window = MainWindow(tmp_path)
    page = window._about_page
    opened = []
    def fail(target):
        opened.append(target)
        raise OSError('no Markdown handler')
    monkeypatch.setattr('e7auto.ui.desktop_actions.open_default', fail)
    page.guide_button.click()
    wait_for(page.guide_button.isEnabled)
    assert '无法打开' in page.status_label.text()
    assert not page.guide_button._spinner_timer.isActive()
    if missing:
        assert '文件不存在' in page.status_label.text() and not opened and not guide.exists()
    else:
        assert opened == [str(guide)] and 'no Markdown handler' in page.status_label.text()
    window.close()


def test_window_close_cancels_queued_about_file_open(tmp_path, monkeypatch):
    (tmp_path / '使用说明.md').write_text('# 使用说明\n', encoding='utf-8')
    window = MainWindow(tmp_path)
    page, opened = window._about_page, []
    monkeypatch.setattr('e7auto.ui.desktop_actions.open_default', lambda target: opened.append(target))
    page.guide_button.click()
    assert page.guide_button._busy
    window.close()
    QTest.qWait(120)
    assert not opened and page.guide_button.isEnabled()
    assert not page.guide_button._spinner_timer.isActive() and not page.guide_button._action_timer.isActive()


def test_about_slow_file_open_keeps_gui_responsive_and_close_waits(tmp_path, monkeypatch):
    from threading import Event, get_ident
    from PySide6.QtCore import QTimer
    entered, release = Event(), Event()
    worker_threads, ticks = [], []
    guide = tmp_path / '使用说明.md'
    guide.write_text('# 使用说明\n', encoding='utf-8')
    def open_target(target):
        assert target == str(guide)
        worker_threads.append(get_ident())
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr('e7auto.ui.desktop_actions.open_default', open_target)
    window = MainWindow(tmp_path)
    page = window._about_page
    window.show()
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    try:
        page.guide_button.click()
        wait_for(entered.is_set)
        assert worker_threads[0] != get_ident()
        before = len(ticks)
        QTest.qWait(50)
        assert len(ticks) > before and not page.guide_button.isEnabled()
        assert not window.close() and page.has_pending_actions()
        release.set()
        wait_for(lambda: not page.has_pending_actions())
        assert not window.isVisible() and page.guide_button.isEnabled()
    finally:
        release.set()
        wait_for(lambda: not page.has_pending_actions())
        timer.stop()
        window.close()
