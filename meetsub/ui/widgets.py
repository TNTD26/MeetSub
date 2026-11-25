from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .theme import COLORS


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, alt: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("CardAlt" if alt else "Card")


class Divider(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)


class Pill(QLabel):
    TONES = ("Pill", "PillGreen", "PillAmber", "PillRed")

    def __init__(self, text: str = "", tone: str = "Pill", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        if tone not in self.TONES:
            tone = "Pill"
        if self.objectName() != tone:
            self.setObjectName(tone)
            self.style().unpolish(self)
            self.style().polish(self)


class StatusDot(QWidget):
    def __init__(self, parent: QWidget | None = None, diameter: int = 9) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self.setFixedSize(diameter + 8, diameter + 8)
        self._color = QColor(COLORS["faint"])
        self._active = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)

    def set_color(self, color: str, active: bool = False) -> None:
        self._color = QColor(color)
        self._active = active
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.20) % (math.tau)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        if self._active:
            radius = self._diameter / 2 + 3.0 + 1.6 * (0.5 + 0.5 * math.sin(self._phase))
            glow = QColor(self._color)
            glow.setAlpha(38)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(center, radius, radius)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(center, self._diameter / 2, self._diameter / 2)
        painter.end()


class LevelMeter(QWidget):
    """A compact, low-cost render of the current WASAPI output level."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._level = 0.0
        self._target = 0.0
        self._active = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(55)
        self._timer.timeout.connect(self._tick)

    def set_level(self, level: float, active: bool = True) -> None:
        self._target = max(0.0, min(1.0, float(level)))
        self._active = active
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active:
            self._timer.stop()
            self._target = 0.0
            self._level = 0.0
            self.update()

    def _tick(self) -> None:
        if not self._active:
            self._timer.stop()
            return
        self._level += (self._target - self._level) * 0.45
        self._phase += 0.17
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        count = 34
        gap = 3
        width = (self.width() - gap * (count - 1)) / count
        if width <= 0:
            painter.end()
            return
        usable = self._level if self._active else 0.0
        color_low = QColor(COLORS["accent"])
        color_high = QColor(COLORS["green"])
        for index in range(count):
            position = index / (count - 1)
            wave = 0.10 + 0.90 * (0.5 + 0.5 * math.sin(self._phase + index * 0.52)) ** 1.7
            bar_height = 3 + usable * wave * (self.height() - 8) * (0.45 + 0.55 * position)
            if usable <= 0:
                bar_height = 3
            color = QColor(
                int(color_low.red() + (color_high.red() - color_low.red()) * position),
                int(color_low.green() + (color_high.green() - color_low.green()) * position),
                int(color_low.blue() + (color_high.blue() - color_low.blue()) * position),
            )
            color.setAlpha(50 + int(190 * usable * (0.4 + 0.6 * position)))
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            x = index * (width + gap)
            y = (self.height() - bar_height) / 2
            painter.drawRoundedRect(QRectF(x, y, width, bar_height), width / 2, width / 2)
        painter.end()


class ElidedLabel(QLabel):
    """A QLabel that elides on one line without changing its height."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setText(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text or ""
        self._refresh()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        metrics = self.fontMetrics()
        elided = metrics.elidedText(self._full_text, Qt.ElideRight, max(20, self.width() - 2))
        super().setText(elided)
        self.setToolTip(self._full_text if elided != self._full_text else "")


def clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        child = item.layout()
        if child is not None:
            clear_layout(child)  # type: ignore[arg-type]


def make_font(family: str, pixel_size: int, bold: bool = False) -> QFont:
    font = QFont(family)
    font.setPixelSize(pixel_size)
    font.setWeight(QFont.DemiBold if bold else QFont.Normal)
    return font


def set_label_color(label: QLabel, color: str) -> None:
    label.setStyleSheet(f"color: {color}; background: transparent;")
