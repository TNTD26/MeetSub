from __future__ import annotations

import ctypes
import logging
import os

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import AppearanceConfig
from .theme import COLORS
from .widgets import StatusDot, make_font

log = logging.getLogger(__name__)


def _blend(foreground: str, background: str, alpha: float) -> str:
    fg = QColor(foreground)
    bg = QColor(background)
    alpha = max(0.0, min(1.0, alpha))
    return QColor(
        int(fg.red() * alpha + bg.red() * (1 - alpha)),
        int(fg.green() * alpha + bg.green() * (1 - alpha)),
        int(fg.blue() * alpha + bg.blue() * (1 - alpha)),
    ).name()


class OverlayPanel(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bg = QColor("#080E19")
        self.opacity = 0.82
        self.radius = 16
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(self.bg)
        color.setAlpha(int(max(0.2, min(1.0, self.opacity)) * 255))
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
        painter.setBrush(color)
        painter.drawRoundedRect(rect, self.radius, self.radius)
        # A very subtle top highlight helps the translucent panel read as glass.
        gradient_color = QColor(255, 255, 255, 10)
        painter.setPen(QPen(gradient_color, 1.0))
        inset = self.radius * 0.6
        painter.drawLine(int(inset + 4), 2, int(self.width() - inset - 4), 2)
        painter.end()


class TurnBlock(QWidget):
    def __init__(self, segment_id: int, family: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.segment_id = segment_id
        self.partial = False
        self.pending_translation = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.source_label = QLabel(self)
        self.source_label.setWordWrap(True)
        self.source_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.source_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.target_label = QLabel(self)
        self.target_label.setWordWrap(True)
        self.target_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.target_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.source_label)
        layout.addWidget(self.target_label)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.source_text = ""
        self.target_text = ""
        self.family = family

    def set_source(self, text: str) -> None:
        self.source_text = text or ""
        self.source_label.setText(self.source_text)
        self.source_label.setVisible(bool(self.source_text))

    def set_target(self, text: str, pending: bool = False, failed: bool = False) -> None:
        self.target_text = text or ""
        self.pending_translation = pending
        if failed:
            self.target_label.setText("翻译暂不可用，仅显示英文")
            self.target_label.setProperty("translationState", "failed")
        elif pending and not self.target_text:
            self.target_label.setText("翻译中…")
            self.target_label.setProperty("translationState", "pending")
        else:
            self.target_label.setText(self.target_text)
            self.target_label.setProperty("translationState", "done")
        self.target_label.setVisible(bool(self.target_label.text()))
        self.target_label.style().unpolish(self.target_label)
        self.target_label.style().polish(self.target_label)


class SubtitleOverlay(QWidget):
    pause_resume_requested = Signal()
    stop_requested = Signal()
    hide_requested = Signal()
    settings_requested = Signal()
    geometry_changed = Signal(int, int)
    font_size_changed = Signal(int, int)
    click_through_changed = Signal(bool)
    privacy_warning = Signal(str)

    def __init__(self, appearance: AppearanceConfig) -> None:
        super().__init__(None)
        self._appearance = AppearanceConfig(**appearance.__dict__)
        self._blocks: list[TurnBlock] = []
        self._state = "idle"
        self._translation_enabled = True
        self._drag_origin: QPoint | None = None
        self._window_origin: QPoint | None = None
        self._dragging = False
        self._drag_moved = False
        self._native_applied = False
        self._warned_capture = False
        self._privacy_checked = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(12)
        self._fit_timer.timeout.connect(self._fit)
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.setInterval(7000)
        self._hint_timer.timeout.connect(self._hide_hint)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = OverlayPanel(self)
        outer.addWidget(self.panel)
        self.panel.setAttribute(Qt.WA_TransparentForMouseEvents)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(24, 13, 24, 16)
        panel_layout.setSpacing(9)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(5)
        self.dot = StatusDot(self.panel, 7)
        self.state_label = QLabel("字幕已就绪", self.panel)
        self.state_label.setFont(make_font("Microsoft YaHei UI", 11))
        self.state_label.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        self.hint_label = QLabel("拖动移动 · 右键更多 · Ctrl+滚轮字号", self.panel)
        self.hint_label.setFont(make_font("Microsoft YaHei UI", 10))
        self.hint_label.setStyleSheet(f"color: {COLORS['faint']}; background: transparent;")
        self.hint_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        header.addWidget(self.dot)
        header.addWidget(self.state_label)
        header.addStretch(1)
        header.addWidget(self.hint_label)
        header.addSpacing(2)
        self.privacy_pill = QLabel("防录屏", self.panel)
        self.privacy_pill.setFont(make_font("Microsoft YaHei UI", 10))
        self.privacy_pill.setStyleSheet(
            "color: rgba(139,155,180,0.75); background: rgba(139,155,180,0.08);"
            "border-radius: 6px; padding: 1px 5px;"
        )
        self.privacy_pill.setAttribute(Qt.WA_TransparentForMouseEvents)
        header.addWidget(self.privacy_pill)
        panel_layout.addLayout(header)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(11)
        panel_layout.addLayout(self.content_layout)

        self.placeholder = QWidget(self.panel)
        placeholder_layout = QVBoxLayout(self.placeholder)
        placeholder_layout.setContentsMargins(0, 3, 0, 2)
        placeholder_layout.setSpacing(2)
        self.placeholder_title = QLabel("等待英文发言…", self.placeholder)
        self.placeholder_title.setFont(make_font("Microsoft YaHei UI", 16, bold=True))
        self.placeholder_title.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        self.placeholder_sub = QLabel(
            "开始后，电脑里播放的会议声音会在这里变成中英双语字幕。",
            self.placeholder,
        )
        self.placeholder_sub.setFont(make_font("Microsoft YaHei UI", 12))
        self.placeholder_sub.setStyleSheet(f"color: {COLORS['faint']}; background: transparent;")
        self.placeholder_sub.setWordWrap(True)
        placeholder_layout.addWidget(self.placeholder_title)
        placeholder_layout.addWidget(self.placeholder_sub)
        self.content_layout.addWidget(self.placeholder)

        self.warning_label = QLabel("", self.panel)
        self.warning_label.setFont(make_font("Microsoft YaHei UI", 10))
        self.warning_label.setStyleSheet(f"color: {COLORS['amber']}; background: transparent;")
        self.warning_label.setVisible(False)
        self.warning_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        panel_layout.addWidget(self.warning_label)

        self._warning_timer = QTimer(self)
        self._warning_timer.setSingleShot(True)
        self._warning_timer.setInterval(6000)
        self._warning_timer.timeout.connect(lambda: self.warning_label.setVisible(False))

        self._apply_appearance()
        self._set_mode("idle")
        self._fit()

    # --------------------------------------------------------------- appearance
    def appearance(self) -> AppearanceConfig:
        return AppearanceConfig(**self._appearance.__dict__)

    def apply_appearance(self, appearance: AppearanceConfig) -> None:
        old_exclude = self._appearance.exclude_from_capture
        self._appearance = AppearanceConfig(**appearance.__dict__)
        self._apply_appearance()
        self._fit(place=True)
        self._apply_native()
        if self.isVisible() and (
            old_exclude != self._appearance.exclude_from_capture or self._appearance.exclude_from_capture
        ):
            try:
                self._apply_display_affinity(int(self.winId()))
            except Exception:
                pass

    def _apply_appearance(self) -> None:
        cfg = self._appearance
        self.panel.bg = QColor(cfg.background_color)
        self.panel.opacity = cfg.background_opacity / 100.0
        self.panel.radius = cfg.corner_radius
        self.panel.update()
        self.privacy_pill.setVisible(cfg.exclude_from_capture)
        self.hint_label.setVisible(True)
        for block in self._blocks:
            self._style_block(block)
        self._set_mode(self._state, update_only=True)

    # --------------------------------------------------------------- lifecycle
    def begin_session(self, translation_enabled: bool = True) -> None:
        self._translation_enabled = translation_enabled
        self.clear_captions()
        self.state_label.setText("正在准备模型…")
        self._set_mode("loading")
        self.warning_label.setVisible(False)
        self._fit(place=True)

    def set_state(self, state: str, detail: str = "") -> None:
        self._state = state
        self._set_mode(state)
        if state == "error" and detail:
            self.warning_label.setText(detail[:140])
            self.warning_label.setVisible(True)
            self._warning_timer.start()
        self._fit()

    def set_translation_enabled(self, enabled: bool) -> None:
        self._translation_enabled = enabled

    def _set_mode(self, state: str, update_only: bool = False) -> None:
        self._state = state
        mapping = {
            "idle": ("字幕已就绪", COLORS["faint"], False),
            "loading": ("正在准备模型…", COLORS["amber"], True),
            "listening": ("正在聆听", COLORS["green"], True),
            "paused": ("已暂停", COLORS["amber"], False),
            "stopping": ("正在保存记录…", COLORS["amber"], True),
            "error": ("字幕服务异常", COLORS["red"], False),
        }
        text, color, active = mapping.get(state, mapping["idle"])
        if update_only and state == "listening" and self._state == "listening":
            pass
        if state == "idle" and self._blocks:
            text = "已停止"
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        self.dot.set_color(color, active=active)
        if state in ("listening", "loading", "stopping"):
            self.placeholder_title.setText(
                "正在聆听，等待英文发言…" if state == "listening" else "正在准备字幕引擎…"
            )
            self.placeholder_sub.setText(
                "现在可以开始会议说话了。字幕会尽量在 1–2 秒内出现。"
                if state == "listening"
                else "首次启动需要加载离线模型，通常只需几秒钟。"
            )
        elif state == "paused":
            self.placeholder_title.setText("字幕已暂停")
            self.placeholder_sub.setText("点击「继续」恢复捕获。暂停期间不会采集任何系统声音。")
        elif state == "error":
            self.placeholder_title.setText("字幕暂时不可用")
            self.placeholder_sub.setText("请查看主窗口提示，或打开设置检查音频与模型。")
        else:
            self.placeholder_title.setText("等待英文发言…")
            self.placeholder_sub.setText("开始后，电脑里播放的会议声音会在这里变成中英双语字幕。")
        self.placeholder.setVisible(not self._blocks)

    def _hide_hint(self) -> None:
        self.hint_label.setVisible(False)
        self._fit()

    # ---------------------------------------------------------------- captions
    def clear_captions(self) -> None:
        for block in self._blocks:
            block.setParent(None)
            block.deleteLater()
        self._blocks.clear()
        self.placeholder.setVisible(True)
        self.warning_label.setVisible(False)
        self._fit()

    def set_partial(self, segment_id: int, text: str) -> None:
        self.placeholder.setVisible(False)
        block = self._find(segment_id)
        if block is None:
            self._trim_for_new_block()
            block = TurnBlock(segment_id, self._appearance.font_family, self.panel)
            self._blocks.append(block)
            self.content_layout.addWidget(block)
        block.partial = True
        block.set_source(text)
        block.set_target("", pending=False)
        self._restyle_all()
        self._fit()

    def clear_partial(self, segment_id: int | None = None) -> None:
        if segment_id is None:
            targets = [b for b in self._blocks if b.partial]
        else:
            targets = [b for b in self._blocks if b.segment_id == segment_id and b.partial]
        for block in targets:
            self._remove_block(block)
        self._restyle_all()
        self._fit()

    def add_source(self, segment_id: int, text: str, translation_pending: bool) -> None:
        self.placeholder.setVisible(False)
        block = self._find(segment_id)
        if block is None:
            self._trim_for_new_block()
            block = TurnBlock(segment_id, self._appearance.font_family, self.panel)
            self._blocks.append(block)
            self.content_layout.addWidget(block)
        block.partial = False
        block.set_source(text)
        block.set_target("", pending=translation_pending)
        self._restyle_all()
        self._fit()

    def translation_started(self, segment_id: int) -> None:
        block = self._find(segment_id)
        if block is not None:
            block.set_target("", pending=True)
            self._fit()

    def update_translation(self, segment_id: int, text: str) -> None:
        block = self._find(segment_id)
        if block is not None:
            block.set_target(text, pending=True)
            self._restyle_all()
            self._fit()

    def finish_translation(self, segment_id: int, text: str) -> None:
        block = self._find(segment_id)
        if block is not None:
            block.set_target(text, pending=False)
            self._restyle_all()
            self._fit()

    def fail_translation(self, segment_id: int, message: str = "") -> None:
        block = self._find(segment_id)
        if block is not None:
            block.set_target("", pending=False, failed=True)
            self._restyle_all()
            self._fit()
        self.show_warning("翻译暂不可用 · 已保留英文原文")
        if message:
            log.info("Translation failure shown to user: %s", message[:160])

    def show_warning(self, text: str) -> None:
        self.warning_label.setText(text[:160])
        self.warning_label.setVisible(True)
        self._warning_timer.start()
        self._fit()

    def _find(self, segment_id: int) -> TurnBlock | None:
        for block in self._blocks:
            if block.segment_id == segment_id:
                return block
        return None

    def _remove_block(self, block: TurnBlock) -> None:
        if block in self._blocks:
            self._blocks.remove(block)
        self.content_layout.removeWidget(block)
        block.setParent(None)
        block.deleteLater()

    def _trim_for_new_block(self) -> None:
        limit = max(1, self._appearance.max_blocks)
        # Keep the current partial plus the newest final turns.
        while len(self._blocks) >= limit:
            self._remove_block(self._blocks[0])

    def _restyle_all(self) -> None:
        limit = max(1, self._appearance.max_blocks)
        while len(self._blocks) > limit:
            self._remove_block(self._blocks[0])
        for block in self._blocks:
            self._style_block(block)

    def _style_block(self, block: TurnBlock) -> None:
        cfg = self._appearance
        index = self._blocks.index(block) if block in self._blocks else 0
        total = max(1, len(self._blocks))
        distance = total - 1 - index
        factor = 1.0 if distance == 0 else (0.74 if distance == 1 else 0.58)
        bg = cfg.background_color
        source_color = _blend(cfg.source_color, bg, factor)
        target_color = _blend(cfg.target_color, bg, factor)
        source_font = make_font(cfg.font_family, cfg.source_font_size)
        target_font = make_font(cfg.font_family, cfg.target_font_size, bold=True)
        block.setFont(source_font)
        block.source_label.setFont(source_font)
        block.target_label.setFont(target_font)
        block.source_label.setStyleSheet(f"color: {source_color}; background: transparent; padding: 0px;")
        state = block.target_label.property("translationState")
        if state == "failed":
            block.target_label.setStyleSheet(
                f"color: {_blend(COLORS['amber'], bg, 0.85)}; background: transparent;"
                f"font-size: {max(11, cfg.target_font_size - 7)}px;"
            )
        elif state == "pending":
            block.target_label.setStyleSheet(
                f"color: {_blend(COLORS['muted'], bg, 0.75)}; background: transparent;"
                f"font-size: {max(11, cfg.target_font_size - 5)}px;"
            )
        else:
            block.target_label.setStyleSheet(f"color: {target_color}; background: transparent;")
        block.source_label.setVisible(cfg.show_source and bool(block.source_text))
        self.content_layout.setSpacing(11 if cfg.target_font_size >= 20 else 8)

    # -------------------------------------------------------------- positioning
    def _screen_for_window(self):
        if self._appearance.position_mode == "free" and self._appearance.position_x is not None:
            screen = QGuiApplication.screenAt(
                QPoint(self._appearance.position_x + 40, (self._appearance.position_y or 0) + 20)
            )
            if screen is not None:
                return screen
        if self.isVisible():
            screen = QGuiApplication.screenAt(self.pos())
            if screen is not None:
                return screen
        return QGuiApplication.primaryScreen()

    def _desired_width(self) -> int:
        screen = self._screen_for_window()
        available = screen.availableGeometry() if screen is not None else None
        max_width = (available.width() - 48) if available else 1600
        return max(480, min(self._appearance.panel_width, max_width))

    def _fit(self, place: bool = False) -> None:
        width = self._desired_width()
        content_width = max(300, width - 48)
        self.panel.setFixedWidth(width)
        if self.placeholder is not None:
            self.placeholder_sub.setFixedWidth(content_width)
            self.placeholder_title.setFixedWidth(content_width)
        for block in self._blocks:
            block.source_label.setFixedWidth(content_width)
            block.target_label.setFixedWidth(content_width)
        self.panel.layout().activate()
        screen = self._screen_for_window()
        max_height = 520
        if screen is not None:
            max_height = min(max_height, int(screen.availableGeometry().height() * 0.60))
        hint_height = self.panel.layout().sizeHint().height()
        # On a narrow overlay or a short screen, drop the oldest captions rather
        # than clipping the newest translation.
        while hint_height > max_height and len(self._blocks) > 1:
            self._remove_block(self._blocks[0])
            self._restyle_all()
            for block in self._blocks:
                block.source_label.setFixedWidth(content_width)
                block.target_label.setFixedWidth(content_width)
            self.panel.layout().activate()
            hint_height = self.panel.layout().sizeHint().height()
        height = max(78, min(int(hint_height), max_height))
        self.setFixedSize(width, height)
        self.panel.setFixedSize(width, height)
        self._place()
        self.panel.update()

    def _place(self) -> None:
        if self._dragging:
            return
        screen = self._screen_for_window()
        if screen is None:
            return
        available = screen.availableGeometry()
        cfg = self._appearance
        width = self.width()
        height = self.height()
        margin = max(8, cfg.screen_margin)
        mode = cfg.position_mode
        if mode == "bottom":
            x = available.x() + (available.width() - width) // 2
            y = available.y() + available.height() - height - margin
        elif mode == "top":
            x = available.x() + (available.width() - width) // 2
            y = available.y() + margin
        else:
            if cfg.position_x is None or cfg.position_y is None:
                x = available.x() + (available.width() - width) // 2
                y = available.y() + available.height() - height - margin
            else:
                x = cfg.position_x
                y = cfg.position_y
            if (
                x + 80 < available.x()
                or x > available.right() - 80
                or y < available.y() - 20
                or y > available.bottom() - 30
            ):
                x = available.x() + (available.width() - width) // 2
                y = available.y() + available.height() - height - margin
        x = max(available.x() + 8, min(x, available.right() - width - 7))
        y = max(available.y() + 8, min(y, available.bottom() - height - 7))
        if self.pos() != QPoint(x, y):
            self.move(x, y)

    def reset_position(self, mode: str = "bottom") -> None:
        self._appearance.position_mode = mode
        if mode != "free":
            self._appearance.position_x = None
            self._appearance.position_y = None
        self._fit(place=True)

    # --------------------------------------------------------------- mouse/input
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._window_origin = self.pos()
            self._drag_moved = False
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging and self._drag_origin is not None and self._window_origin is not None:
            current = event.globalPosition().toPoint()
            delta = current - self._drag_origin
            if not self._drag_moved and delta.manhattanLength() > 3:
                self._drag_moved = True
            self.move(self._window_origin + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            if self._drag_moved:
                self._appearance.position_mode = "free"
                self._appearance.position_x = self.x()
                self._appearance.position_y = self.y()
                self.geometry_changed.emit(self.x(), self.y())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.settings_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            direction = 1 if event.angleDelta().y() > 0 else -1
            cfg = self._appearance
            new_source = max(10, min(34, cfg.source_font_size + direction))
            new_target = max(12, min(42, cfg.target_font_size + direction))
            if (new_source, new_target) != (cfg.source_font_size, cfg.target_font_size):
                cfg.source_font_size = new_source
                cfg.target_font_size = new_target
                self._apply_appearance()
                self._fit()
                self.font_size_changed.emit(new_source, new_target)
            event.accept()
            return
        super().wheelEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self._show_menu(event.globalPos())
        event.accept()

    def _show_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        active = self._state in ("listening", "paused", "loading", "stopping")
        if self._state == "paused":
            action = menu.addAction("继续字幕")
            action.triggered.connect(self.pause_resume_requested.emit)
        elif self._state == "listening":
            action = menu.addAction("暂停字幕")
            action.triggered.connect(self.pause_resume_requested.emit)
        else:
            action = menu.addAction("开始实时字幕")
            action.triggered.connect(self.pause_resume_requested.emit)
        action.setEnabled(self._state != "loading")
        if active:
            stop_action = menu.addAction("结束本次记录")
            stop_action.triggered.connect(self.stop_requested.emit)
        menu.addSeparator()
        bigger = menu.addAction("增大字号")
        bigger.triggered.connect(lambda: self._nudge_font(1))
        smaller = menu.addAction("减小字号")
        smaller.triggered.connect(lambda: self._nudge_font(-1))
        lock = menu.addAction("鼠标穿透（锁定位置）")
        lock.setCheckable(True)
        lock.setChecked(self._appearance.click_through)
        lock.toggled.connect(self._set_click_through)
        menu.addSeparator()
        hide = menu.addAction("隐藏字幕")
        hide.triggered.connect(self.hide_requested.emit)
        settings = menu.addAction("设置…")
        settings.triggered.connect(self.settings_requested.emit)
        menu.exec(pos)

    def _nudge_font(self, direction: int) -> None:
        cfg = self._appearance
        cfg.source_font_size = max(10, min(34, cfg.source_font_size + direction))
        cfg.target_font_size = max(12, min(42, cfg.target_font_size + direction))
        self._apply_appearance()
        self._fit()
        self.font_size_changed.emit(cfg.source_font_size, cfg.target_font_size)

    def _set_click_through(self, enabled: bool) -> None:
        self._appearance.click_through = bool(enabled)
        self._apply_native()
        self.click_through_changed.emit(bool(enabled))

    # ------------------------------------------------------------------ native
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._after_show)

    def _after_show(self) -> None:
        self._apply_native()
        self.raise_()
        self.hint_label.setVisible(True)
        self._hint_timer.start()
        self._fit(place=True)

    def _apply_native(self) -> None:
        if os.name != "nt" or not self.isVisible():
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
            get_long.restype = ctypes.c_longlong
            set_long.restype = ctypes.c_longlong
            get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_TRANSPARENT = 0x00000020
            ex_style = int(get_long(hwnd, GWL_EXSTYLE))
            ex_style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            if self._appearance.click_through:
                ex_style |= WS_EX_TRANSPARENT
            else:
                ex_style &= ~WS_EX_TRANSPARENT
            set_long(hwnd, GWL_EXSTYLE, ex_style)
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020
            HWND_TOPMOST = -1
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
            if not self._privacy_checked:
                self._privacy_checked = True
                self._apply_display_affinity(hwnd)
        except Exception as exc:
            log.debug("Could not apply overlay native styles: %s", exc)

    def _apply_display_affinity(self, hwnd: int) -> None:
        try:
            user32 = ctypes.windll.user32
            WDA_NONE = 0x00000000
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            WDA_MONITOR = 0x00000001
            if self._appearance.exclude_from_capture:
                ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
                if not ok:
                    ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR))
                if not ok and not self._warned_capture:
                    self._warned_capture = True
                    self.privacy_warning.emit(
                        "当前系统不支持将字幕排除在屏幕共享之外（需要 Windows 10 2004 或更新版本）。"
                    )
                    self.privacy_pill.setVisible(False)
            else:
                user32.SetWindowDisplayAffinity(hwnd, WDA_NONE)
                self.privacy_pill.setVisible(False)
        except Exception:
            if not self._warned_capture:
                self._warned_capture = True
                self.privacy_warning.emit("无法启用防录屏保护，请谨慎共享屏幕。")

    def show_overlay(self) -> None:
        self._fit(place=True)
        self.show()
        self.raise_()
        self._after_show()

    def hide_overlay(self) -> None:
        self.hide()
