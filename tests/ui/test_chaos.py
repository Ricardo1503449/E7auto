from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, qInstallMessageHandler
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QStyle, QStyleFactory, QStyleOptionComboBox, QStylePainter
from types import SimpleNamespace
import pytest

from e7auto.core.domain import OverlayActivityStatus, RuntimeSnapshot, StopReason
from e7auto.ui import MainWindow
from tests.helpers import make_config
from tests.helpers import FakeLogger
from e7auto.ui.worker import AutomationWorker


def test_chaos_defeat_row_keeps_fixed_size_and_survives_collapse():
    from dataclasses import replace
    from e7auto.ui import StatsOverlay

    app = QApplication.instance() or QApplication([])
    overlay = StatsOverlay()
    try:
        overlay.configure_chaos(make_config())
        overlay.show()
        app.processEvents()
        size = overlay.size()
        snapshot = replace(RuntimeSnapshot.chaos_run("large", 50), defeats=9999999999)
        overlay.update_snapshot(snapshot)
        app.processEvents()
        assert overlay.size() == size
        label = overlay._defeats
        assert label.contentsRect().width() >= label.fontMetrics().horizontalAdvance(label.text())
        assert label.contentsRect().height() >= label.fontMetrics().height()
        overlay._collapse_button.click()
        assert not label.isVisible()
        overlay.update_snapshot(snapshot.with_chaos_defeat())
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=overlay.rect().center())
        assert label.isVisible() and label.text() == "战败次数：10000000000"
        assert overlay.size() == size
        assert label.toolTip() == ""
    finally:
        overlay.close()
        app.processEvents()


def test_chaos_settings_positive_limit_and_existing_overlay_lifecycle(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        window._function_center_page.chaos_card.click()
        page = window._chaos_feature_page
        assert window._pages.currentWidget() is page
        assert page.findChildren(QLineEdit) == [page.limit_input]
        assert page.limit_value() == 1
        assert page.target_stage()==1 and page.stage_input.count()==5
        assert page.lineup_id() == 'dark_twins'
        assert [page.lineup_input.itemText(i) for i in range(2)] == ['暗双子队', '平民替换队']
        for stage in range(1,6):
            page.stage_input.setCurrentIndex(stage-1)
            assert page.stage_input.currentText()==f'第{stage}阶段' and page.target_stage()==stage
        for text in ("0", "-1", "", "1.2", "2147483648", "abc"):
            page.limit_input.setText(text)
            assert page.limit_value() is None
            assert not page.start_button.isEnabled()
        page.limit_input.setText("2")
        assert page.start_button.isEnabled()
        overlay = window._overlay
        overlay.configure_chaos(make_config())
        snapshot = RuntimeSnapshot.chaos_run("x", 2).with_chaos_round().with_chaos_defeat()
        overlay.update_snapshot(snapshot)
        assert overlay._currency.text() == "已完成轮数：1 / 2"
        assert overlay._defeats.text() == "战败次数：1"
        assert not overlay._defeats.isHidden()
        assert overlay._defeats.y() > overlay._currency.geometry().bottom()
        assert overlay._status.y() > overlay._defeats.geometry().bottom()
        assert overlay._status.text() == "当前状态：运行中"
        assert not any("点击左上方停止按钮结束" in label.text() for label in overlay.findChildren(QLabel))
        assert overlay._no_target.isHidden()
        overlay.update_snapshot(snapshot.with_overlay_status(OverlayActivityStatus.RECONNECTING))
        assert overlay._status.text() == "当前状态：重连中"
        overlay.update_snapshot(snapshot.with_chaos_round().finalized(StopReason.CHAOS_COMPLETE))
        assert overlay._status.text() == "当前状态：已完成"
        assert overlay._dismissible
        assert overlay._defeats.text() == "战败次数：1"
        overlay.configure_penguins(make_config())
        overlay.update_snapshot(RuntimeSnapshot.penguins("y", 3))
        assert overlay._currency.text() == "已购买次数：0 / 3"
        assert overlay._defeats.isHidden()
        overlay.configure(make_config())
        assert overlay._defeats.isHidden()
        overlay.configure_chaos(make_config())
        overlay.update_snapshot(RuntimeSnapshot.chaos_run("z", 1).finalized(StopReason.MANUAL_STOP))
        assert overlay._status.text() == "当前状态：已停止"
        assert overlay._defeats.text() == "战败次数：0"
    finally:
        window.close();app.processEvents()


@pytest.mark.parametrize('setup_fails',[False,True])
def test_worker_dispatch_and_setup_failure_keep_chaos_identity(tmp_path,monkeypatch,setup_fails):
    app=QApplication.instance() or QApplication([])
    logger=FakeLogger();calls=[];finals=[]
    monkeypatch.setattr('e7auto.ui.worker.RunLogManager',lambda *_:SimpleNamespace(start=lambda _:logger))
    def factory(config,feature,overlay,log,callback,control):
        calls.append(feature)
        if setup_fails:raise RuntimeError('synthetic setup error')
        def run(limit,run_id):
            calls.append(limit)
            return RuntimeSnapshot.chaos_run(run_id,limit).with_chaos_round().with_chaos_round().finalized(StopReason.CHAOS_COMPLETE)
        return SimpleNamespace(run_chaos=run)
    monkeypatch.setattr('e7auto.ui.worker.create_production_session',factory)
    worker=AutomationWorker(make_config(),0,False,tmp_path,None,round_limit=2)
    worker.finished.connect(finals.append)
    worker.run();app.processEvents()
    assert calls[0]=='chaos' and finals[0].feature_id=='chaos' and finals[0].round_limit==2
    assert finals[0].stop_reason==(StopReason.INTERNAL_ERROR if setup_fails else StopReason.CHAOS_COMPLETE)


def test_selected_stage_is_passed_to_run_configuration(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path);captured=[]
    monkeypatch.setattr('e7auto.ui.preparation.load_config',lambda *args,**kwargs:make_config())
    def capture(config,*,target_stage,lineup_id):
        captured.append((target_stage,lineup_id))
        raise RuntimeError('stop before starting worker')
    monkeypatch.setattr('e7auto.ui.preparation.with_chaos_config',capture)
    try:
        window._chaos_feature_page.stage_input.setCurrentIndex(2)
        window._chaos_feature_page.lineup_input.setCurrentIndex(1)
        from tests.helpers.qt import wait_for
        window._start_chaos_run()
        wait_for(lambda: window._preparation_thread is None)
        assert 'stop before starting worker' in window._chaos_feature_page.status_label.text()
        assert captured==[(3,'budget')] and window._thread is None
    finally:
        window.close();app.processEvents()


def test_lineup_selection_keyboard_label_and_disabled_state(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        page = window._chaos_feature_page
        window._pages.setCurrentWidget(page);window.show();app.processEvents()
        combo = page.lineup_input
        label = next(w for w in page.findChildren(QLabel) if w.text() == '阵容')
        assert label.buddy() is combo and combo.accessibleName() == '阵容'
        combo.setFocus();QTest.keyClick(combo, Qt.Key.Key_End)
        assert page.lineup_id() == 'budget'
        page.setEnabled(False);QTest.keyClick(combo, Qt.Key.Key_Home)
        assert page.lineup_id() == 'budget'
        page.setEnabled(True);QTest.keyClick(combo, Qt.Key.Key_Home)
        assert page.lineup_id() == 'dark_twins'
        assert page.start_button.geometry().bottom() <= page.start_button.parentWidget().height()
    finally:
        window.close();app.processEvents()


@pytest.mark.parametrize('field,label_text', [('stage_input','目标阶段'), ('lineup_input','阵容')])
def test_stage_combo_font_and_arrow_geometry_match_settings(tmp_path, field, label_text):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path)
    try:
        page=window._chaos_feature_page
        window._pages.setCurrentWidget(page)
        window.ensurePolished();page.ensurePolished()
        combo=getattr(page,field);combo.ensurePolished();combo.view().ensurePolished()
        label=next(w for w in page.findChildren(QLabel) if w.text()==label_text)
        assert combo.font().families()==label.font().families()==combo.view().font().families()
        assert combo.font().pixelSize()==label.font().pixelSize()==combo.view().font().pixelSize()==18
        option=QStyleOptionComboBox();combo.initStyleOption(option)
        arrow=combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxArrow,combo)
        text=combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,combo)
        assert arrow.width()==36 and combo.rect().contains(arrow)
        assert text.right()<arrow.left()
    finally:
        window.close();app.processEvents()


@pytest.mark.parametrize('fails',[False,True])
def test_stage_popup_suppresses_only_its_animation_and_restores_preference(monkeypatch,fails):
    from e7auto.ui.pages.chaos import _StageComboBox
    app=QApplication.instance() or QApplication([])
    combo=_StageComboBox();state={'animated':True};seen=[]
    monkeypatch.setattr(QApplication,'isEffectEnabled',lambda effect:state['animated'])
    def set_effect(effect,value):
        assert effect==Qt.UIEffect.UI_AnimateCombo
        state['animated']=value
    monkeypatch.setattr(QApplication,'setEffectEnabled',set_effect)
    def show(_):
        seen.append(state['animated'])
        if fails:raise RuntimeError('popup failure')
    monkeypatch.setattr(QComboBox,'showPopup',show)
    try:
        if fails:
            with pytest.raises(RuntimeError,match='popup failure'):combo.showPopup()
        else:combo.showPopup()
        assert seen==[False] and state['animated'] is True
    finally:
        combo.close();app.processEvents()


def test_stage_popup_repeated_mouse_and_keyboard_selection_is_stable(tmp_path):
    class VisibilityEvents(QObject):
        def __init__(self):super().__init__();self.events=[]
        def eventFilter(self,watched,event):
            if event.type() in (QEvent.Type.Show,QEvent.Type.Hide):self.events.append(event.type())
            return False
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path);window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    page=window._chaos_feature_page;window._pages.setCurrentWidget(page)
    combo=page.stage_input;view=combo.view();popup=view.window()
    popup.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    observer=VisibilityEvents();popup.installEventFilter(observer)
    activated=QSignalSpy(combo.activated)
    try:
        window.show();app.processEvents()
        popup_sizes=[]
        for index in (1,4,2):
            QTest.mouseClick(combo,Qt.MouseButton.LeftButton,pos=QPoint(combo.width()-18,combo.height()//2))
            app.processEvents()
            assert popup.isVisible() and view.isVisible()
            popup_sizes.append(popup.size())
            row=view.model().index(index,0)
            point=view.visualRect(row).center()
            # A real pointer travels from the combo to the row; that movement
            # clears Qt's guard against the opening click selecting an item.
            QApplication.sendEvent(view.viewport(),QMouseEvent(
                QEvent.Type.MouseMove,QPointF(point),QPointF(view.viewport().mapToGlobal(point)),
                Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,
            ))
            QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point)
            app.processEvents()
            assert not popup.isVisible() and page.target_stage()==index+1
        assert activated.count()==3 and all(s==popup_sizes[0] for s in popup_sizes)
        # Opening from the text area must behave identically; Escape doesn't commit.
        QTest.mouseClick(combo,Qt.MouseButton.LeftButton,pos=QPoint(24,combo.height()//2));app.processEvents()
        QTest.keyClick(view,Qt.Key.Key_Down)
        QTest.keyClick(view,Qt.Key.Key_Escape);app.processEvents()
        assert not popup.isVisible() and page.target_stage()==3 and activated.count()==3
        combo.setFocus();QTest.keyClick(combo,Qt.Key.Key_Space);app.processEvents()
        assert popup.isVisible()
        QTest.keyClick(view,Qt.Key.Key_Down);QTest.keyClick(view,Qt.Key.Key_Return);app.processEvents()
        assert not popup.isVisible() and page.target_stage()==4 and activated.count()==4
        assert observer.events==[QEvent.Type.Show,QEvent.Type.Hide]*5
    finally:
        combo.hidePopup();window.close();app.processEvents()


def test_stage_caption_and_popup_items_are_centered(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path);window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    page=window._chaos_feature_page;window._pages.setCurrentWidget(page)
    combo=page.stage_input;draws=[];original=QStylePainter.drawItemText
    def record(painter,rect,flags,palette,enabled,text,role):
        if text in [f'第{n}阶段' for n in range(1,6)]:draws.append((rect,flags,text))
        return original(painter,rect,flags,palette,enabled,text,role)
    monkeypatch.setattr(QStylePainter,'drawItemText',record)
    try:
        window.show();app.processEvents()
        combo.setCurrentIndex(4);combo.update();app.processEvents()
        assert draws and draws[-1][2]=='第5阶段'
        for rect,flags,_ in draws:
            assert flags==Qt.AlignmentFlag.AlignCenter
            assert rect.center()==combo.rect().center()
            assert rect.left()>0 and rect.right()<combo.width()-36
        for index in range(5):
            assert combo.itemData(index,Qt.ItemDataRole.TextAlignmentRole)==Qt.AlignmentFlag.AlignCenter
    finally:
        window.close();app.processEvents()


def test_windows11_stage_popup_has_no_invalid_font_warning(tmp_path):
    if 'windows11' not in QStyleFactory.keys():pytest.skip('Windows 11 style unavailable')
    from e7auto.ui.pages.chaos import _StageComboStyle
    app=QApplication.instance() or QApplication([])
    app_style=app.style();messages=[]
    previous=qInstallMessageHandler(lambda kind,context,message:messages.append(message))
    window=None
    try:
        window=MainWindow(tmp_path);window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        page=window._chaos_feature_page;window._pages.setCurrentWidget(page)
        combo=page.stage_input
        if app_style.objectName()!='windows11':
            style=_StageComboStyle('windows11');style.setParent(combo);combo.setStyle(style)
        window.show();app.processEvents()
        combo.view().window().setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        initial_font=combo.font()
        for index in range(5):
            combo.setCurrentIndex(index);combo.showPopup();app.processEvents()
            assert combo.view().isVisible()
            combo.hidePopup();app.processEvents()
        assert not any('QFont::setPointSize' in message for message in messages),messages
        assert combo.font()==initial_font and combo.font().pixelSize()==18
        assert combo.width()==260 and combo.height()==56
        assert app.style() is app_style
    finally:
        if window is not None:window.close();app.processEvents()
        qInstallMessageHandler(previous)
