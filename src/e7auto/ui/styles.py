"""Shared desktop style values; colors and geometry retain the approved appearance."""
from string import Template

COLORS = {
    "surface": "#ffffff",
    "background": "#f2f3f5",
    "text": "#15181b",
    "icon": "#454c54",
    "muted": "#68717b",
    "disabled": "#a2a7ad",
    "border": "#e3e5e8",
    "primary": "#26985a",
    "primary_hover": "#228a50",
    "primary_pressed": "#1d7846",
    "selection": "#e9f2fb",
    "accent": "#1677d2",
    "update_dot": "#e5484d",
    "link": "#007bff",
    "link_border": "#80b8ff"
}
FONT_FAMILY = '"Microsoft YaHei UI", "Segoe UI"'
HEADING_SIZE = 24
LABEL_SIZE = 18
PAGE_MARGINS = (32, 28, 32, 32)
CARD_MARGINS = (30, 26, 30, 30)
CONTROL_SIZE = (260, 56)
FORM_SPACING = 24

MAIN_WINDOW_STYLE = Template("""
QMainWindow { background: transparent; }
QMainWindow[nativeFrame="true"] { background: ${background}; }
QMainWindow[nativeFrame="true"] QFrame#appShell { border: none; border-radius: 0; }
QMainWindow[nativeFrame="true"] QWidget#titleBar {
    border-top-left-radius: 0;
    border-top-right-radius: 0;
}
QMainWindow[nativeFrame="true"] QFrame#sidebar { border-bottom-left-radius: 0; }
QMainWindow[nativeFrame="true"] QFrame#updateDialogBackdrop {
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
}
QFrame#appShell {
    background: ${background};
    border: 1px solid #cfd3d7;
    border-radius: 12px;
}
QFrame#appShell[windowMaximized="true"] {
    border: none;
    border-radius: 0;
}
QWidget#titleBar {
    background: ${surface};
    border-bottom: 1px solid ${border};
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
}
QWidget#titleBar[windowMaximized="true"] {
    border-top-left-radius: 0;
    border-top-right-radius: 0;
}
QLabel#titleBarIcon { border-radius: 4px; }
QLabel#titleBarVersion { color: ${muted}; font-size: 14px; }
QWidget#appBody { background: transparent; }
QFrame#updateDialogBackdrop {
    background: rgba(0, 0, 0, 128);
    border: none;
    border-bottom-left-radius: 11px;
    border-bottom-right-radius: 11px;
}
QFrame#sidebar {
    background: ${surface};
    border: none;
    border-right: 1px solid ${border};
    border-bottom-left-radius: 11px;
}
QPushButton[navigationButton="true"] {
    background: transparent;
    border: none;
    outline: none;
    border-radius: 8px;
    color: ${icon};
    font-family: ${font_family};
    font-size: 15px;
    text-align: left;
    padding: 0 0 0 44px;
}
QPushButton[navigationButton="true"]:hover { background: #f0f2f4; }
QPushButton[navigationButton="true"]:pressed { background: #e7eaee; }
QPushButton[navigationButton="true"]:checked { background: ${selection}; }
QPushButton[navigationButton="true"]:disabled { color: ${disabled}; }
QLabel#windowTitle {
    color: #14171a;
    font-family: "Segoe UI";
    font-size: ${label_size}px;
    font-weight: 600;
}
QPushButton[windowControl="true"] {
    border: none;
    border-radius: 7px;
    background: transparent;
    color: #111417;
}
QPushButton[windowControl="true"]:hover { background: ${border}; }
QPushButton#closeButton:hover { background: #e5565d; color: white; }
QStackedWidget#pageStack,
QWidget#functionCenterPage,
QWidget#shopFeaturePage,
QWidget#penguinFeaturePage, QWidget#chaosFeaturePage,
QWidget#aboutPage, QWidget#applicationSettingsPage,
QWidget#applicationContent,
QScrollArea#applicationScrollArea,
QScrollArea#applicationScrollArea > QWidget > QWidget,
QWidget#moduleCardHost,
QWidget#featureContent,
QScrollArea#moduleScrollArea,
QScrollArea#featureScrollArea,
QScrollArea#moduleScrollArea > QWidget > QWidget,
QScrollArea#featureScrollArea > QWidget > QWidget {
    background: transparent;
    border: none;
}
QLabel {
    color: ${text};
    font-family: ${font_family};
    font-size: 17px;
}
QLabel#settingLabel { font-size: ${label_size}px; }
QFrame#applicationCard {
    background: ${surface};
    border: 1px solid ${border};
    border-radius: 14px;
}
QPushButton[settingsRow="true"] {
    background: transparent;
    border: 1px solid transparent;
    outline: none;
    border-radius: 8px;
}
QPushButton[settingsRow="true"]:hover { background: #f6f7f8; }
QPushButton[settingsRow="true"]:pressed { background: #edf0f2; }
QPushButton[settingsRow="true"] QLabel { background: transparent; }
QPushButton[settingsRow="true"] QLabel:disabled { color: ${disabled}; }
QLabel#settingValue, QLabel#settingsStatus { color: ${text}; font-size: 14px; }
QLabel#shopStatisticsPending, QLabel#shopStatisticsStatus { color: ${text}; font-size: 16px; }
QPushButton#shopStatisticsRetry {
    color: ${primary}; background: ${surface}; border: 1px solid ${primary};
    border-radius: 6px; padding: 8px 16px; font-size: 16px;
}
QPushButton#shopStatisticsRetry:hover { background: #e9f5ee; }
QPushButton#shopStatisticsRetry:disabled { color: ${disabled}; }
QLabel#aboutVersion { color: ${muted}; }
QLabel#applicationHeading { font-size: ${heading_size}px; font-weight: 700; }
QLabel#aboutTitle { font-size: ${heading_size}px; font-weight: 600; }
QFrame#settingsDivider { background: #f0f1f3; border: none; }
QFrame#updateResultCard {
    background: ${surface};
    border: 1px solid #dfe3e8;
    border-radius: 14px;
}
QLabel#updateResultHeading { font-size: ${heading_size}px; font-weight: 600; }
QLabel#updateInstallStatus { color: ${text}; font-size: 14px; }
QProgressBar#updateDownloadProgress {
    border: 1px solid ${border}; border-radius: 5px; background: ${background};
    color: ${text}; text-align: center; min-height: 20px; font-size: 12px;
}
QProgressBar#updateDownloadProgress::chunk { background: ${primary}; border-radius: 4px; }
QLabel#updateResultMessage { color: ${text}; font-size: 16px; }
QTextBrowser#updateReleaseNotes {
    background: ${surface};
    color: ${text};
    border: none;
    font-family: ${font_family};
    font-size: 15px;
}
QTextBrowser#updateReleaseNotes QScrollBar:vertical {
    width: 8px; margin: 0; border: none; background: transparent;
}
QTextBrowser#updateReleaseNotes QScrollBar::handle:vertical {
    min-height: 32px; border: none; border-radius: 4px; background: #c6cbd0;
}
QTextBrowser#updateReleaseNotes QScrollBar::handle:vertical:hover { background: #adb5bd; }
QTextBrowser#updateReleaseNotes QScrollBar::handle:vertical:pressed { background: #929ca5; }
QTextBrowser#updateReleaseNotes QScrollBar::add-line:vertical,
QTextBrowser#updateReleaseNotes QScrollBar::sub-line:vertical {
    width: 0; height: 0; border: none; background: transparent;
}
QTextBrowser#updateReleaseNotes QScrollBar::add-page:vertical,
QTextBrowser#updateReleaseNotes QScrollBar::sub-page:vertical { background: transparent; }
QPushButton#updateDialogCloseButton, QPushButton#updateDialogActionButton {
    background: ${surface};
    color: ${link};
    border: 1px solid ${link_border};
    border-radius: 7px;
    padding: 0 16px;
    font-family: ${font_family};
    font-size: 16px;
}
QPushButton#updateDialogCloseButton:hover { background: #f0f7ff; border-color: #409aff; }
QPushButton#updateDialogCloseButton:pressed { background: #e3efff; border-color: ${link}; }
QPushButton#updateDialogActionButton { background: ${accent}; color: ${surface}; border-color: ${accent}; }
QPushButton#updateDialogActionButton:hover { background: #1267b8; }
QPushButton#updateDialogCloseButton:focus { border-color: ${link}; }
QPushButton#updateDialogActionButton:focus { border-color: #074b91; }
QFrame#settingsCard {
    background: ${surface};
    border: 1px solid #d7dade;
    border-radius: 14px;
}
QLineEdit#refreshLimitInput, QLineEdit#penguinPurchaseLimitInput, QLineEdit#chaosRoundLimitInput,
QLabel#penguinLeafCostDisplay {
    background: #fbfbfc;
    color: #111417;
    border: 1px solid #d3d6da;
    border-radius: 9px;
    padding: 0 18px;
    font-family: "Segoe UI";
    font-size: 20px;
}
QLineEdit#refreshLimitInput:focus { border: 1px solid #8c9299; }
QLineEdit#penguinPurchaseLimitInput:focus, QLineEdit#chaosRoundLimitInput:focus { border: 1px solid #8c9299; }
QComboBox[chaosSetting="true"] {
    background: #fbfbfc;
    color: ${text};
    border: 1px solid #d3d6da;
    border-radius: 9px;
    padding: 0 42px 0 18px;
    font-family: ${font_family};
    font-size: ${label_size}px;
}
QComboBox[chaosSetting="true"]:hover { border-color: #aeb4bc; }
QComboBox[chaosSetting="true"]:focus, QComboBox[chaosSetting="true"]:on { border-color: ${primary}; }
QComboBox[chaosSetting="true"]:disabled { color: #9298a0; background: ${background}; }
QComboBox[chaosSetting="true"]::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 36px;
    border: none;
    background: transparent;
}
QComboBox[chaosSetting="true"]::down-arrow { image: none; }
QListView[chaosSetting="true"] {
    background: ${surface};
    color: ${text};
    border: 1px solid #d3d6da;
    padding: 4px;
    font-family: ${font_family};
    font-size: ${label_size}px;
    selection-background-color: #e5f3eb;
    selection-color: #176d3c;
    outline: none;
}
QListView[chaosSetting="true"]::item { min-height: 36px; padding: 0 12px; }
QListView[chaosSetting="true"]::item:selected { background: #e5f3eb; color: #176d3c; }
QPushButton#startButton, QPushButton#penguinStartButton, QPushButton#chaosStartButton {
    background: ${primary};
    color: ${surface};
    border: none;
    border-radius: 10px;
    font-size: 20px;
    font-weight: 600;
}
QPushButton#startButton:hover, QPushButton#penguinStartButton:hover, QPushButton#chaosStartButton:hover { background: ${primary_hover}; }
QPushButton#startButton:pressed, QPushButton#penguinStartButton:pressed, QPushButton#chaosStartButton:pressed { background: ${primary_pressed}; }
QPushButton#startButton:disabled, QPushButton#penguinStartButton:disabled, QPushButton#chaosStartButton:disabled { background: #b8bdc1; color: #eef0f1; }
QScrollBar:vertical {
    width: 10px;
    background: transparent;
    margin: 2px;
}
QScrollBar::handle:vertical {
    min-height: 34px;
    background: #c6cbd0;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
""").substitute(COLORS, font_family=FONT_FAMILY, heading_size=HEADING_SIZE, label_size=LABEL_SIZE)
