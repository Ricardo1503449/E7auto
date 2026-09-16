"""Keep one application and release only widgets created by each UI test."""
import gc

import pytest
from PySide6.QtWidgets import QApplication

from tests.helpers.qt import dispose_widgets


@pytest.fixture(scope="session")
def qt_application():
    application = QApplication.instance() or QApplication([])
    previous = application.quitOnLastWindowClosed()
    application.setQuitOnLastWindowClosed(False)
    yield application
    application.setQuitOnLastWindowClosed(previous)


@pytest.fixture(autouse=True)
def release_ui_widgets(qt_application):
    existing = {id(widget) for widget in qt_application.topLevelWidgets()}
    yield
    widgets = [widget for widget in qt_application.topLevelWidgets() if id(widget) not in existing]
    dispose_widgets(qt_application, widgets)
    del widgets
    gc.collect()
