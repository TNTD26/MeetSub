from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import AppConfig
from ..models import missing_required
from ..translator import translator_description
from .theme import COLORS, app_icon, app_icon_pixmap, set_dark_titlebar
from .widgets import Card, LevelMeter, Pill, StatusDot, make_font


class ControlWindow(QWidget):
    start_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    overlay_toggle_requested = Signal(bool)
    settings_requested = Signal()
    setup_requested = Signal()
    save_requested = Signal()
    open_records_requested = Signal()
    hide_requested = Signal()
    close_requested = Signal()

    STATE_LABELS = {
        "idle": ("准备就绪", COLORS["faint"]),
        "loading": ("正在准备字幕引擎", COLORS["amber"]),
        "listening": ("正在聆听", COLORS["green"]),
        "paused": ("已暂停", COLORS["amber"]),
        "stopping": ("正在保存记录", COLORS["amber"]),
        "error": ("需要处理", COLORS["red"]),
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("会译字幕 · MeetSub")
        self.setWindowIcon(app_icon())
        self.setObjectName("Root")
        self.setMinimumSize(430, 540)
        self.resize(466, 622)
        self._state = "idle"
        self._last_source = ""
        self._last_target = ""
        self._session_started: float | None = None
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._clear_notice)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick)
        self._build()
        self._refresh_clock()

    # ------------------------------------------------------------------- layout
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(11)

        header = QHBoxLayout()
        header.setSpacing(11)
        logo = QLabel(self)
        pix = app_icon_pixmap(56)
        logo.setPixmap(pix.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.setFixedSize(42, 42)
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand = QLabel("会译字幕", self)
        brand.setObjectName("Brand")
        sub = QLabel("MEETSUB · 实时英中会议字幕", self)
        sub.setObjectName("BrandSub")
        brand_box.addWidget(brand)
        brand_box.addWidget(sub)
        header.addWidget(logo)
        header.addLayout(brand_box)
        header.addStretch(1)
        self.local_pill = Pill("本地优先", "PillGreen", self)
        header.addWidget(self.local_pill)
        outer.addLayout(header)

        self.setup_card = QFrame(self)
        self.setup_card.setObjectName("CardAlt")
        setup_layout = QHBoxLayout(self.setup_card)
        setup_layout.setContentsMargins(12, 9, 10, 9)
        setup_layout.setSpacing(8)
        setup_text = QVBoxLayout()
        setup_text.setSpacing(1)
        self.setup_title = QLabel("首次使用需要准备离线模型", self.setup_card)
        self.setup_title.setStyleSheet("font-weight: 600; color: #F1B45A; background: transparent;")
        self.setup_detail = QLabel("下载一次，之后识别与翻译都可离线运行。", self.setup_card)
        self.setup_detail.setObjectName("Tiny")
        self.setup_detail.setWordWrap(True)
        setup_text.addWidget(self.setup_title)
        setup_text.addWidget(self.setup_detail)
        setup_layout.addLayout(setup_text, 1)
        self.setup_button = QPushButton("准备模型", self.setup_card)
        self.setup_button.setObjectName("SetupButton")
        self.setup_button.clicked.connect(self.setup_requested.emit)
        setup_layout.addWidget(self.setup_button)
        self.setup_card.setVisible(False)
        outer.addWidget(self.setup_card)

        self.status_card = Card(self)
        status_layout = QVBoxLayout(self.status_card)
        status_layout.setContentsMargins(16, 13, 16, 13)
        status_layout.setSpacing(8)
        state_row = QHBoxLayout()
        state_row.setSpacing(6)
        self.dot = StatusDot(self.status_card, 9)
        self.state_label = QLabel("准备就绪", self.status_card)
        self.state_label.setObjectName("StateText")
        self.clock_label = QLabel("", self.status_card)
        self.clock_label.setObjectName("Tiny")
        self.clock_label.setFont(make_font("Cascadia Mono", 11))
        state_row.addWidget(self.dot)
        state_row.addWidget(self.state_label)
        state_row.addStretch(1)
        state_row.addWidget(self.clock_label)
        status_layout.addLayout(state_row)
        self.status_label = QLabel(
            "点击开始后自动捕获电脑播放的会议声音；无需选择麦克风。",
            self.status_card,
        )
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(34)
        status_layout.addWidget(self.status_label)
        self.level_meter = LevelMeter(self.status_card)
        status_layout.addWidget(self.level_meter)
        self.device_label = QLabel("音频输出：自动选择系统默认扬声器", self.status_card)
        self.device_label.setObjectName("Tiny")
        status_layout.addWidget(self.device_label)
        outer.addWidget(self.status_card)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.primary_button = QPushButton("开始实时字幕", self)
        self.primary_button.setObjectName("PrimaryButton")
        self.primary_button.setCursor(Qt.PointingHandCursor)
        self.primary_button.clicked.connect(self._primary_action)
        action_row.addWidget(self.primary_button, 1)
        self.stop_button = QPushButton("结束", self)
        self.stop_button.setObjectName("StopButton")
        self.stop_button.setFixedWidth(78)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.stop_button.setVisible(False)
        action_row.addWidget(self.stop_button)
        outer.addLayout(action_row)

        secondary = QHBoxLayout()
        secondary.setSpacing(7)
        self.overlay_button = QPushButton("隐藏字幕", self)
        self.overlay_button.setObjectName("Chip")
        self.overlay_button.setCheckable(True)
        self.overlay_button.setChecked(True)
        self.overlay_button.setToolTip("显示或隐藏悬浮字幕（不会停止记录）")
        self.overlay_button.toggled.connect(self._overlay_toggled)
        self.save_button = QPushButton("保存记录", self)
        self.save_button.setObjectName("Chip")
        self.save_button.clicked.connect(self.save_requested.emit)
        self.settings_button = QPushButton("设置", self)
        self.settings_button.setObjectName("Chip")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.hide_button = QPushButton("收起", self)
        self.hide_button.setObjectName("Chip")
        self.hide_button.setToolTip("收起到系统托盘，字幕与识别继续运行")
        self.hide_button.clicked.connect(self.hide_requested.emit)
        for button in (self.overlay_button, self.save_button, self.settings_button, self.hide_button):
            button.setCursor(Qt.PointingHandCursor)
            secondary.addWidget(button)
        outer.addLayout(secondary)

        self.latest_card = Card(self)
        latest_layout = QVBoxLayout(self.latest_card)
        latest_layout.setContentsMargins(15, 11, 15, 12)
        latest_layout.setSpacing(5)
        latest_head = QHBoxLayout()
        latest_title = QLabel("最新字幕", self.latest_card)
        latest_title.setObjectName("SectionLabel")
        latest_head.addWidget(latest_title)
        latest_head.addStretch(1)
        self.provider_pill = Pill("本地翻译", "PillGreen", self.latest_card)
        latest_head.addWidget(self.provider_pill)
        latest_layout.addLayout(latest_head)
        self.latest_source = QLabel("开始后，这里会显示最近一句英文原声。", self.latest_card)
        self.latest_source.setObjectName("Muted")
        self.latest_source.setWordWrap(True)
        self.latest_source.setMaximumHeight(38)
        self.latest_target = QLabel("中文翻译会同步出现在这里。", self.latest_card)
        self.latest_target.setWordWrap(True)
        self.latest_target.setStyleSheet("font-size: 15px; color: #EAF1FB; background: transparent;")
        self.latest_target.setMaximumHeight(48)
        latest_layout.addWidget(self.latest_source)
        latest_layout.addWidget(self.latest_target)
        outer.addWidget(self.latest_card)

        self.notice_label = QLabel("", self)
        self.notice_label.setWordWrap(True)
        self.notice_label.setVisible(False)
        self.notice_label.setStyleSheet(
            "color: #F1B45A; background: rgba(241,180,90,0.08); border-radius: 8px; padding: 7px 9px;"
        )
        outer.addWidget(self.notice_label)

        outer.addStretch(1)
        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.privacy_label = QLabel("音频只在本机识别 · 无上传", self)
        self.privacy_label.setObjectName("Tiny")
        self.privacy_label.setWordWrap(True)
        version = QLabel(f"v{__version__}", self)
        version.setObjectName("Tiny")
        footer.addWidget(self.privacy_label, 1)
        footer.addWidget(version)
        outer.addLayout(footer)

    # -------------------------------------------------------------------- state
    def state(self) -> str:
        return self._state

    def set_state(self, state: str, status: str = "") -> None:
        previous = self._state
        self._state = state
        label, color = self.STATE_LABELS.get(state, self.STATE_LABELS["idle"])
        self.state_label.setText(label)
        self.dot.set_color(color, active=state in ("loading", "listening", "stopping"))
        if status:
            self.status_label.setText(status)
        if state == "listening":
            if previous not in ("paused", "listening"):
                self._session_started = time.monotonic()
            self.primary_button.setText("暂停")
            self.primary_button.setEnabled(True)
            self.stop_button.setVisible(True)
            self.clock_label.setVisible(True)
            self.level_meter.set_level(0.0, active=True)
            self._clock_timer.start()
        elif state == "paused":
            self.primary_button.setText("继续")
            self.primary_button.setEnabled(True)
            self.stop_button.setVisible(True)
            self.level_meter.set_level(0.0, active=False)
            self._clock_timer.stop()
            self._refresh_clock()
        elif state == "loading":
            self.primary_button.setText("正在准备…")
            self.primary_button.setEnabled(False)
            self.stop_button.setVisible(False)
            self.clock_label.setVisible(False)
            self.level_meter.set_level(0.0, active=False)
            self._clock_timer.stop()
        elif state == "stopping":
            self.primary_button.setText("正在保存…")
            self.primary_button.setEnabled(False)
            self.stop_button.setVisible(False)
            self.level_meter.set_level(0.0, active=False)
            self._clock_timer.stop()
        elif state == "error":
            self.primary_button.setText("重试")
            self.primary_button.setEnabled(True)
            self.stop_button.setVisible(False)
            self.clock_label.setVisible(False)
            self.level_meter.set_level(0.0, active=False)
            self._clock_timer.stop()
        else:
            self.primary_button.setText("开始实时字幕")
            self.primary_button.setEnabled(True)
            self.stop_button.setVisible(False)
            self.clock_label.setVisible(False)
            self.level_meter.set_level(0.0, active=False)
            self._clock_timer.stop()
            self._session_started = None
            self._refresh_clock()
        self._style_primary(state)
        self._refresh_clock()

    def _style_primary(self, state: str) -> None:
        if state in ("listening", "paused"):
            self.primary_button.setStyleSheet(
                "QPushButton#PrimaryButton { background: #172338; border: 1px solid #2A3B57;"
                " color: #EAF1FB; font-weight: 600; min-height: 48px; border-radius: 13px; }"
                "QPushButton#PrimaryButton:hover { background: #1D2C45; }"
            )
        else:
            self.primary_button.setStyleSheet("")

    def set_status(self, text: str) -> None:
        if text and self.status_label.text() != text:
            self.status_label.setText(text)

    def set_level(self, level: float) -> None:
        self.level_meter.set_level(level, active=self._state == "listening")

    def set_device(self, name: str, fallback_note: str = "") -> None:
        if name:
            self.device_label.setText(f"音频输出：{name}")
        if fallback_note:
            self.show_notice(fallback_note, timeout=7000)

    def set_overlay_visible(self, visible: bool) -> None:
        self.overlay_button.blockSignals(True)
        self.overlay_button.setChecked(visible)
        self.overlay_button.setText("隐藏字幕" if visible else "显示字幕")
        self.overlay_button.blockSignals(False)

    def _overlay_toggled(self, checked: bool) -> None:
        self.overlay_button.setText("隐藏字幕" if checked else "显示字幕")
        self.overlay_toggle_requested.emit(checked)

    def _primary_action(self) -> None:
        if self._state == "paused":
            self.resume_requested.emit()
        elif self._state == "listening":
            self.pause_requested.emit()
        elif self._state in ("idle", "error"):
            self.start_requested.emit()

    def _tick(self) -> None:
        self._refresh_clock()

    def _refresh_clock(self) -> None:
        if self._session_started is None or self._state not in ("listening", "paused"):
            if self._state not in ("listening", "paused"):
                self.clock_label.setText("")
            return
        elapsed = max(0, int(time.monotonic() - self._session_started))
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        self.clock_label.setText(
            f"● {hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"● {minutes:02d}:{seconds:02d}"
        )

    # ------------------------------------------------------------------ content
    def set_partial(self, source: str) -> None:
        self.latest_source.setText(source or "")
        self.latest_target.setText("识别中…")
        self.latest_target.setStyleSheet("font-size: 14px; color: #71829B; background: transparent;")
        self._last_source = source

    def set_final_source(self, source: str) -> None:
        self.latest_source.setText(source or "")
        self._last_source = source
        self.latest_target.setText("翻译中…")
        self.latest_target.setStyleSheet("font-size: 14px; color: #71829B; background: transparent;")

    def set_translation(self, text: str) -> None:
        self._last_target = text or ""
        if text:
            self.latest_target.setText(text)
            self.latest_target.setStyleSheet("font-size: 15px; color: #EAF1FB; background: transparent;")
        else:
            self.latest_target.setText("仅显示英文")
            self.latest_target.setStyleSheet("font-size: 14px; color: #71829B; background: transparent;")

    def translation_failed(self) -> None:
        self.latest_target.setText("翻译暂不可用，已保留英文")
        self.latest_target.setStyleSheet("font-size: 13px; color: #F1B45A; background: transparent;")

    def clear_latest(self) -> None:
        self._last_source = ""
        self._last_target = ""
        self.latest_source.setText("开始后，这里会显示最近一句英文原声。")
        self.latest_source.setStyleSheet("")
        self.latest_source.setObjectName("Muted")
        self.latest_target.setText("中文翻译会同步出现在这里。")
        self.latest_target.setStyleSheet("font-size: 15px; color: #EAF1FB; background: transparent;")

    def show_notice(self, text: str, timeout: int = 6500) -> None:
        self.notice_label.setText(text)
        self.notice_label.setVisible(True)
        self._notice_timer.start(timeout)

    def _clear_notice(self) -> None:
        self.notice_label.setVisible(False)

    def refresh_config(self, config: AppConfig, has_api_key: bool = False) -> None:
        missing = missing_required(config)
        if missing:
            total = sum(spec.approx_mb for spec in missing)
            self.setup_title.setText("需要准备离线模型")
            self.setup_detail.setText(
                f"还需下载约 {total / 1024:.1f} GB；下载一次后可完全离线使用。"
                if total >= 1024
                else f"还需下载约 {total} MB；下载一次后可完全离线使用。"
            )
            self.setup_button.setText("准备模型")
            self.setup_card.setVisible(True)
        else:
            self.setup_card.setVisible(False)

        provider = config.translation.provider
        if provider == "offline":
            if config.translation.offline_model == "qwen2.5-1.5b":
                self.provider_pill.setText("本地 LLM")
            else:
                self.provider_pill.setText("本地翻译")
            self.provider_pill.set_tone("PillGreen")
            self.privacy_label.setText("音频与文字只在本机处理 · 无云端上传 · 不录音")
            self.local_pill.setText("完全本地")
            self.local_pill.set_tone("PillGreen")
        elif provider == "ollama":
            self.provider_pill.setText("Ollama 本地")
            self.provider_pill.set_tone("PillGreen")
            self.privacy_label.setText("音频在本机识别 · 字幕文字发送至本机 Ollama")
            self.local_pill.setText("本机大模型")
            self.local_pill.set_tone("PillGreen")
        elif provider == "openai":
            self.provider_pill.setText("在线翻译")
            self.provider_pill.set_tone("PillAmber" if has_api_key else "PillRed")
            self.privacy_label.setText("音频仅在本机识别 · 字幕文字会发送至配置的翻译服务")
            self.local_pill.setText("文本云端" if has_api_key else "未配密钥")
            self.local_pill.set_tone("PillAmber" if has_api_key else "PillRed")
        else:
            self.provider_pill.setText("仅英文")
            self.provider_pill.set_tone("Pill")
            self.privacy_label.setText("音频只在本机处理 · 翻译已关闭 · 不录音")
            self.local_pill.setText("仅转写")
            self.local_pill.set_tone("Pill")
        self.provider_pill.setToolTip(translator_description(config))

        self.save_button.setEnabled(True)
        self.overlay_button.setEnabled(True)

    def set_save_enabled(self, enabled: bool) -> None:
        self.save_button.setEnabled(enabled)

    # ------------------------------------------------------------------- window
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, lambda: set_dark_titlebar(self))
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            target_h = min(self.height(), max(520, available.height() - 54))
            target_w = min(self.width(), max(430, available.width() - 80))
            if target_h != self.height() or target_w != self.width():
                self.resize(target_w, target_h)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.close_requested.emit()
