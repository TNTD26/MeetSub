from __future__ import annotations

import ctypes
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FONT = "Cascadia Mono"

COLORS = {
    "bg": "#090E16",
    "panel": "#101827",
    "panel2": "#141E30",
    "border": "#213049",
    "border_soft": "#1A2638",
    "text": "#EAF1FB",
    "muted": "#8B9BB4",
    "faint": "#5D6C84",
    "accent": "#5B9CFF",
    "accent2": "#7C6CFF",
    "green": "#43D6A0",
    "amber": "#F1B45A",
    "red": "#FF7088",
    "chip": "#172338",
}

QSS = f"""
* {{
    font-family: "{FONT_FAMILY}";
    outline: none;
}}
QWidget {{
    color: {COLORS["text"]};
    font-size: 13px;
}}
QWidget#Root, QDialog {{
    background-color: {COLORS["bg"]};
}}
QMainWindow {{
    background-color: {COLORS["bg"]};
}}
QFrame#Card {{
    background-color: {COLORS["panel"]};
    border: 1px solid {COLORS["border_soft"]};
    border-radius: 16px;
}}
QFrame#CardAlt {{
    background-color: {COLORS["panel2"]};
    border: 1px solid {COLORS["border_soft"]};
    border-radius: 14px;
}}
QFrame#Divider {{
    background-color: {COLORS["border_soft"]};
    max-height: 1px;
    border: none;
}}
QLabel {{
    background: transparent;
}}
QLabel#Brand {{
    font-size: 20px;
    font-weight: 700;
    color: {COLORS["text"]};
}}
QLabel#BrandSub {{
    font-size: 11px;
    color: {COLORS["muted"]};
}}
QLabel#StateText {{
    font-size: 17px;
    font-weight: 600;
    color: {COLORS["text"]};
}}
QLabel#Muted {{
    color: {COLORS["muted"]};
}}
QLabel#Tiny {{
    color: {COLORS["faint"]};
    font-size: 11px;
}}
QLabel#SectionLabel {{
    color: {COLORS["muted"]};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#Pill {{
    background-color: {COLORS["chip"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 9px;
    padding: 3px 9px;
    color: {COLORS["muted"]};
    font-size: 11px;
}}
QLabel#PillGreen {{
    background-color: rgba(67, 214, 160, 0.10);
    border: 1px solid rgba(67, 214, 160, 0.30);
    border-radius: 9px;
    padding: 3px 9px;
    color: {COLORS["green"]};
    font-size: 11px;
}}
QLabel#PillAmber {{
    background-color: rgba(241, 180, 90, 0.10);
    border: 1px solid rgba(241, 180, 90, 0.30);
    border-radius: 9px;
    padding: 3px 9px;
    color: {COLORS["amber"]};
    font-size: 11px;
}}
QLabel#PillRed {{
    background-color: rgba(255, 112, 136, 0.10);
    border: 1px solid rgba(255, 112, 136, 0.30);
    border-radius: 9px;
    padding: 3px 9px;
    color: {COLORS["red"]};
    font-size: 11px;
}}
QPushButton {{
    border-radius: 10px;
    padding: 8px 14px;
    background-color: {COLORS["panel2"]};
    border: 1px solid {COLORS["border_soft"]};
    color: {COLORS["text"]};
}}
QPushButton:hover {{
    background-color: #1A263B;
    border-color: {COLORS["border"]};
}}
QPushButton:pressed {{
    background-color: #0E1725;
}}
QPushButton:disabled {{
    color: #4E5C72;
    background-color: #111927;
    border-color: #182337;
}}
QPushButton#PrimaryButton {{
    min-height: 48px;
    font-size: 15px;
    font-weight: 600;
    border: none;
    border-radius: 13px;
    color: white;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4C8DFF, stop:1 #7464FF);
    padding: 12px 18px;
}}
QPushButton#PrimaryButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5B9AFF, stop:1 #8273FF);
}}
QPushButton#PrimaryButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3E7BE8, stop:1 #6557E8);
}}
QPushButton#PrimaryButton:disabled {{
    color: #A4B2C9;
    background: #1B2940;
}}
QPushButton#SecondaryButton {{
    min-height: 38px;
    background-color: transparent;
    border: 1px solid {COLORS["border"]};
    color: {COLORS["text"]};
    border-radius: 11px;
    padding: 7px 13px;
}}
QPushButton#SecondaryButton:hover {{
    background-color: {COLORS["panel2"]};
    border-color: #354866;
}}
QPushButton#StopButton {{
    min-height: 38px;
    background-color: transparent;
    border: 1px solid rgba(255, 112, 136, 0.32);
    color: {COLORS["red"]};
    border-radius: 11px;
    padding: 7px 16px;
}}
QPushButton#StopButton:hover {{
    background-color: rgba(255, 112, 136, 0.08);
}}
QPushButton#Chip {{
    min-height: 32px;
    border-radius: 10px;
    background-color: transparent;
    border: 1px solid {COLORS["border_soft"]};
    color: {COLORS["muted"]};
    padding: 5px 11px;
    font-size: 12px;
}}
QPushButton#Chip:hover {{
    color: {COLORS["text"]};
    background-color: {COLORS["panel2"]};
    border-color: {COLORS["border"]};
}}
QPushButton#Chip:checked {{
    color: {COLORS["accent"]};
    border-color: rgba(91, 156, 255, 0.45);
    background-color: rgba(91, 156, 255, 0.08);
}}
QPushButton#LinkButton {{
    background: transparent;
    border: none;
    color: {COLORS["accent"]};
    padding: 3px 2px;
    font-size: 12px;
    text-align: left;
}}
QPushButton#LinkButton:hover {{
    color: #83B6FF;
}}
QPushButton#SetupButton {{
    background-color: rgba(241, 180, 90, 0.10);
    border: 1px solid rgba(241, 180, 90, 0.28);
    color: {COLORS["amber"]};
    border-radius: 9px;
    padding: 6px 12px;
    font-size: 12px;
    min-height: 0;
}}
QPushButton#SetupButton:hover {{
    background-color: rgba(241, 180, 90, 0.16);
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QFontComboBox {{
    background-color: #0D1522;
    border: 1px solid {COLORS["border"]};
    border-radius: 9px;
    padding: 7px 9px;
    color: {COLORS["text"]};
    min-height: 20px;
    selection-background-color: #2E64B8;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QFontComboBox:focus {{
    border-color: {COLORS["accent"]};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {COLORS["faint"]};
    background-color: #0B121D;
}}
QComboBox::drop-down, QFontComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: #111A29;
    border: 1px solid {COLORS["border"]};
    selection-background-color: #1D3558;
    color: {COLORS["text"]};
    outline: none;
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 18px;
    border: none;
    background: transparent;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: #243249;
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {COLORS["accent"]};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: #DCE9FF;
    border: 2px solid {COLORS["accent"]};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 8px;
}}
QCheckBox {{
    spacing: 8px;
    color: {COLORS["text"]};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
}}
QTabWidget::pane {{
    border: 1px solid {COLORS["border_soft"]};
    border-radius: 12px;
    background-color: {COLORS["panel"]};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {COLORS["muted"]};
    padding: 8px 14px;
    margin-right: 2px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {COLORS["text"]};
    border-bottom: 2px solid {COLORS["accent"]};
}}
QTabBar::tab:hover {{
    color: {COLORS["text"]};
}}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #2A3952;
    min-height: 24px;
    border-radius: 4px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QProgressBar {{
    border: 1px solid {COLORS["border"]};
    border-radius: 7px;
    background-color: #0C1420;
    text-align: center;
    color: {COLORS["text"]};
    min-height: 14px;
    font-size: 11px;
}}
QProgressBar::chunk {{
    border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4C8DFF, stop:1 #7464FF);
}}
QMenu {{
    background-color: #121B2A;
    border: 1px solid {COLORS["border"]};
    border-radius: 10px;
    padding: 6px;
    color: {COLORS["text"]};
}}
QMenu::item {{
    padding: 7px 26px 7px 12px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: #1D3558;
}}
QMenu::separator {{
    height: 1px;
    background: {COLORS["border_soft"]};
    margin: 5px 8px;
}}
QToolTip {{
    background-color: #182337;
    color: {COLORS["text"]};
    border: 1px solid {COLORS["border"]};
    padding: 6px 8px;
    border-radius: 6px;
}}
QMessageBox {{
    background-color: {COLORS["bg"]};
}}
QMessageBox QLabel {{
    color: {COLORS["text"]};
}}
QGroupBox {{
    border: 1px solid {COLORS["border_soft"]};
    border-radius: 11px;
    margin-top: 13px;
    padding: 14px 12px 10px 12px;
    color: {COLORS["muted"]};
    background: transparent;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: {COLORS["muted"]};
    font-size: 11px;
    font-weight: 600;
}}
QPlainTextEdit, QTextEdit {{
    background-color: #0D1522;
    border: 1px solid {COLORS["border"]};
    border-radius: 9px;
    color: {COLORS["text"]};
    padding: 7px;
}}
QListWidget {{
    background-color: #0D1522;
    border: 1px solid {COLORS["border"]};
    border-radius: 9px;
    padding: 4px;
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont(FONT_FAMILY)
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(QSS)


def set_dark_titlebar(widget: QWidget) -> None:
    if os.name != "nt":
        return
    try:
        hwnd = int(widget.winId())
        enabled = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, fallback
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
        # A dark title bar needs a dark border on Windows 11; harmless on 10.
        color = ctypes.c_int(0x00110D09)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(color), ctypes.sizeof(color))
    except Exception:
        pass


def app_icon_pixmap(size: int = 256) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#4C8DFF"))
    gradient.setColorAt(1.0, QColor("#7464FF"))
    painter.setBrush(gradient)
    painter.setPen(Qt.NoPen)
    margin = size * 0.055
    painter.drawRoundedRect(
        int(margin), int(margin), int(size - margin * 2), int(size - margin * 2), size * 0.24, size * 0.24
    )
    # Subtle inner caption bars.
    painter.setBrush(QColor(255, 255, 255, 34))
    painter.drawRoundedRect(
        int(size * 0.22),
        int(size * 0.64),
        int(size * 0.56),
        int(size * 0.07),
        int(size * 0.035),
        int(size * 0.035),
    )
    painter.drawRoundedRect(
        int(size * 0.30),
        int(size * 0.76),
        int(size * 0.40),
        int(size * 0.05),
        int(size * 0.025),
        int(size * 0.025),
    )
    font = QFont(FONT_FAMILY)
    font.setPixelSize(int(size * 0.43))
    font.setWeight(QFont.Bold)
    painter.setFont(font)
    painter.setPen(QPen(QColor("#FFFFFF")))
    painter.drawText(
        int(size * 0.14), int(size * 0.14), int(size * 0.72), int(size * 0.52), Qt.AlignCenter, "译"
    )
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon
