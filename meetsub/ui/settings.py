from __future__ import annotations

import copy
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..audio import AudioCaptureError, list_loopback_endpoints
from ..config import (
    WHISPER_MODELS,
    AppConfig,
    AppearanceConfig,
    SecretsVault,
)
from ..models import (
    asr_spec,
    spec_ready,
    translation_spec,
)
from ..paths import data_dir, log_dir
from ..translator import OpenAICompatibleTranslator, create_translator
from ..utils import open_directory
from .model_setup import ModelSetupDialog
from .theme import COLORS, app_icon, app_icon_pixmap, set_dark_titlebar
from .widgets import Card, make_font

log = logging.getLogger(__name__)


def _slider_row(slider: QSlider, value_label: QLabel) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(9)
    layout.addWidget(slider, 1)
    value_label.setMinimumWidth(62)
    value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    value_label.setObjectName("Muted")
    layout.addWidget(value_label)
    return widget


class ColorButton(QPushButton):
    color_changed = Signal(str)

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        if QColor(color).isValid():
            self._color = color
            self._refresh()

    def _refresh(self) -> None:
        self.setText(self._color.upper())
        self.setStyleSheet(
            f"QPushButton {{ text-align: left; padding: 7px 10px 7px 30px;"
            f" background-color: {self._color}; color: {'#081018' if QColor(self._color).lightness() > 150 else '#FFFFFF'};"
            f" border: 1px solid rgba(255,255,255,0.20); border-radius: 8px; font-size: 11px; }}"
        )

    def _pick(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "选择颜色")
        if color.isValid():
            self._color = color.name()
            self._refresh()
            self.color_changed.emit(self._color)


class OverlayPreview(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Preview")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 14)
        layout.setSpacing(4)
        head = QLabel("字幕预览", self)
        head.setFont(make_font("Microsoft YaHei UI", 10))
        head.setStyleSheet("color: #8B9BB4; background: transparent;")
        self.source = QLabel("We should align on the launch date before Friday.", self)
        self.source.setWordWrap(True)
        self.target = QLabel("我们应该在周五之前确认上线日期。", self)
        self.target.setWordWrap(True)
        layout.addWidget(head)
        layout.addWidget(self.source)
        layout.addWidget(self.target)
        self.setMinimumHeight(118)

    def update_preview(self, cfg: AppearanceConfig) -> None:
        bg = QColor(cfg.background_color)
        self.setStyleSheet(
            "QFrame#Preview {"
            f"background-color: rgba({bg.red()},{bg.green()},{bg.blue()},{cfg.background_opacity});"
            "border: 1px solid rgba(255,255,255,0.12);"
            f"border-radius: {max(0, cfg.corner_radius)}px;"
            "}"
        )
        self.source.setFont(make_font(cfg.font_family, cfg.source_font_size))
        self.source.setStyleSheet(f"color: {cfg.source_color}; background: transparent;")
        self.source.setVisible(cfg.show_source)
        self.target.setFont(make_font(cfg.font_family, cfg.target_font_size, bold=True))
        self.target.setStyleSheet(f"color: {cfg.target_color}; background: transparent;")
        self.setMinimumHeight(
            max(96, cfg.target_font_size * 3 + (cfg.source_font_size * 2 if cfg.show_source else 0))
        )


class _TestBridge(QObject):
    ok = Signal(str)
    fail = Signal(str)


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, vault: SecretsVault, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = copy.deepcopy(config).normalize()
        self.original = copy.deepcopy(config).normalize()
        self.vault = vault
        self._key_cleared = False
        self._test_thread: threading.Thread | None = None
        self._test_bridge: _TestBridge | None = None
        self._test_cancel = threading.Event()
        self._loading_values = False
        self.setWindowTitle("设置 · 会译字幕")
        self.setWindowIcon(app_icon())
        self.setModal(True)
        self.resize(790, 680)
        self.setMinimumSize(720, 580)
        self._build()
        self._load_values()
        QTimer.singleShot(0, lambda: set_dark_titlebar(self))

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("设置", self)
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        subtitle = QLabel("设置保存在本机；密钥使用 Windows DPAPI 加密。", self)
        subtitle.setObjectName("Muted")
        header.addWidget(title)
        header.addSpacing(10)
        header.addWidget(subtitle)
        header.addStretch(1)
        root.addLayout(header)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_audio_tab(), "音频与识别")
        self.tabs.addTab(self._build_translation_tab(), "翻译")
        self.tabs.addTab(self._build_appearance_tab(), "字幕外观")
        self.tabs.addTab(self._build_records_tab(), "记录与隐私")
        self.tabs.addTab(self._build_about_tab(), "关于")
        root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        note = QLabel("音频、识别与翻译改动会在下次开始字幕时生效；外观改动保存后立即生效。", self)
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        footer.addWidget(note, 1)
        cancel = QPushButton("取消", self)
        cancel.setObjectName("SecondaryButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存设置", self)
        save.setObjectName("PrimaryButton")
        save.setFixedWidth(152)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

    def _scroll_page(self) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 12, 14, 16)
        layout.setSpacing(13)
        scroll.setWidget(content)
        return scroll, layout

    def _form(self, parent: QWidget | None = None) -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return form

    def _group(self, title: str) -> tuple[QGroupBox, QVBoxLayout]:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(8)
        return box, layout

    def _build_audio_tab(self) -> QWidget:
        page, layout = self._scroll_page()

        box, box_layout = self._group("音频采集（系统声音 · WASAPI 回环）")
        form = self._form(box)
        self.device_combo = QComboBox(box)
        self.device_combo.setMinimumWidth(320)
        device_row = QWidget(box)
        device_layout = QHBoxLayout(device_row)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(7)
        device_layout.addWidget(self.device_combo, 1)
        refresh = QPushButton("刷新", box)
        refresh.setObjectName("Chip")
        refresh.clicked.connect(self._reload_devices)
        device_layout.addWidget(refresh)
        form.addRow("输出设备", device_row)
        box_layout.addLayout(form)
        hint = QLabel(
            "选择「自动」会跟随 Windows 当前默认扬声器/耳机。采集的是电脑正在播放的声音，不会打开麦克风。",
            box,
        )
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        box_layout.addWidget(hint)
        layout.addWidget(box)

        box2, layout2 = self._group("端点检测")
        form2 = self._form(box2)
        self.vad_combo = QComboBox(box2)
        self.vad_combo.addItem("低 · 安静环境，更敏感", 1)
        self.vad_combo.addItem("标准 · 推荐", 2)
        self.vad_combo.addItem("高 · 嘈杂环境，减少误触发", 3)
        self.vad_combo.addItem("关闭降噪过滤 · 实验性", 0)
        form2.addRow("人声检测", self.vad_combo)
        self.silence_slider = QSlider(Qt.Horizontal)
        self.silence_slider.setRange(300, 1500)
        self.silence_slider.setSingleStep(50)
        self.silence_label = QLabel("650 ms")
        form2.addRow("停顿定稿", _slider_row(self.silence_slider, self.silence_label))
        self.max_segment_spin = QDoubleSpinBox()
        self.max_segment_spin.setRange(5.0, 20.0)
        self.max_segment_spin.setSingleStep(1.0)
        self.max_segment_spin.setSuffix(" 秒")
        form2.addRow("最长分段", self.max_segment_spin)
        layout2.addLayout(form2)
        note2 = QLabel("停顿越长越完整但字幕落定更慢；推荐 650 ms。", box2)
        note2.setObjectName("Tiny")
        layout2.addWidget(note2)
        layout.addWidget(box2)

        box3, layout3 = self._group("语音识别（faster-whisper · 本地）")
        form3 = self._form(box3)
        self.model_combo = QComboBox(box3)
        for model in WHISPER_MODELS:
            spec = asr_spec(model)
            self.model_combo.addItem(f"{spec.label} · {spec.size_label}", model)
        form3.addRow("识别模型", self.model_combo)
        self.model_status = QLabel("")
        self.model_status.setObjectName("Tiny")
        self.model_status.setWordWrap(True)
        form3.addRow("", self.model_status)
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 32)
        form3.addRow("CPU 线程", self.threads_spin)
        self.hotwords_edit = QLineEdit()
        self.hotwords_edit.setPlaceholderText("人名/产品名，逗号分隔，例如：Kubernetes, Tom Chen")
        form3.addRow("识别热词", self.hotwords_edit)
        self.compute_combo = QComboBox()
        self.compute_combo.addItem("int8 · 推荐，内存更少、速度更快", "int8")
        self.compute_combo.addItem("float32 · 更精确，但更慢、更占内存", "float32")
        form3.addRow("计算精度", self.compute_combo)
        self.model_source_combo = QComboBox()
        self.model_source_combo.addItem("国内镜像 · hf-mirror.com（推荐）", "https://hf-mirror.com")
        self.model_source_combo.addItem("Hugging Face 官方", "https://huggingface.co")
        form3.addRow("模型源", self.model_source_combo)
        layout3.addLayout(form3)
        model_actions = QHBoxLayout()
        self.model_detail_label = QLabel(box3)
        self.model_detail_label.setObjectName("Tiny")
        self.model_detail_label.setWordWrap(True)
        model_actions.addWidget(self.model_detail_label, 1)
        self.download_model_button = QPushButton("准备模型…", box3)
        self.download_model_button.setObjectName("Chip")
        self.download_model_button.clicked.connect(self._open_model_setup)
        model_actions.addWidget(self.download_model_button)
        layout3.addLayout(model_actions)
        layout.addWidget(box3)
        layout.addStretch(1)
        self.model_combo.currentIndexChanged.connect(self._update_model_status)

        self.silence_slider.valueChanged.connect(lambda value: self.silence_label.setText(f"{value} ms"))
        return page

    def _build_translation_tab(self) -> QWidget:
        page, layout = self._scroll_page()

        top = Card(page)
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(15, 13, 15, 13)
        top_layout.setSpacing(4)
        heading = QLabel("翻译方式", top)
        heading.setStyleSheet("font-weight: 600;")
        desc = QLabel(
            "默认使用本地 Qwen2.5 LLM，可理解会议背景、术语表和上下文；也可切换轻量模型或连接 "
            "DeepSeek / OpenAI 兼容接口 / 本机 Ollama。",
            top,
        )
        desc.setObjectName("Tiny")
        desc.setWordWrap(True)
        top_layout.addWidget(heading)
        top_layout.addWidget(desc)
        self.provider_combo = QComboBox(top)
        self.provider_combo.addItem("本地离线翻译 · 完全不上传（推荐）", "offline")
        self.provider_combo.addItem("DeepSeek / OpenAI 兼容接口 · 质量更好", "openai")
        self.provider_combo.addItem("Ollama 本机大模型", "ollama")
        self.provider_combo.addItem("仅转写，不翻译", "none")
        top_layout.addWidget(self.provider_combo)
        layout.addWidget(top)

        self.offline_group, offline_layout = self._group("离线翻译模型")
        oform = self._form(self.offline_group)
        self.offline_model_combo = QComboBox()
        for key in ("qwen2.5-1.5b", "m2m100-418m", "opus-mt"):
            spec = translation_spec(key)
            suffix = {
                "qwen2.5-1.5b": " · 准确优先",
                "m2m100-418m": " · 平衡",
                "opus-mt": " · 低资源",
            }[key]
            self.offline_model_combo.addItem(f"{spec.label}{suffix} · {spec.size_label}", key)
        oform.addRow("翻译模型", self.offline_model_combo)
        self.offline_status = QLabel("")
        self.offline_status.setObjectName("Tiny")
        self.offline_status.setWordWrap(True)
        oform.addRow("", self.offline_status)
        offline_layout.addLayout(oform)
        off_actions = QHBoxLayout()
        self.offline_detail = QLabel(self.offline_group)
        self.offline_detail.setObjectName("Tiny")
        self.offline_detail.setWordWrap(True)
        off_actions.addWidget(self.offline_detail, 1)
        self.download_offline_button = QPushButton("下载模型…", self.offline_group)
        self.download_offline_button.setObjectName("Chip")
        self.download_offline_button.clicked.connect(self._open_translation_model_setup)
        off_actions.addWidget(self.download_offline_button)
        offline_layout.addLayout(off_actions)
        self.offline_model_combo.currentIndexChanged.connect(self._update_offline_status)
        layout.addWidget(self.offline_group)

        self.openai_group, online_layout = self._group("在线翻译接口")
        form = self._form(self.openai_group)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("DeepSeek（推荐）", "deepseek")
        self.preset_combo.addItem("OpenAI", "openai")
        self.preset_combo.addItem("自定义 OpenAI 兼容服务", "custom")
        form.addRow("服务预设", self.preset_combo)
        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setPlaceholderText("https://api.deepseek.com/v1")
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("deepseek-chat")
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-…")
        self.key_hint = QLabel("")
        self.key_hint.setObjectName("Tiny")
        self.key_hint.setWordWrap(True)
        self.clear_key_button = QPushButton("清除已保存密钥")
        self.clear_key_button.setObjectName("LinkButton")
        self.clear_key_button.clicked.connect(self._clear_key)
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_edit, 1)
        key_layout.addWidget(self.clear_key_button)
        form.addRow("接口地址", self.endpoint_edit)
        form.addRow("模型名称", self.model_edit)
        form.addRow("API 密钥", key_row)
        form.addRow("", self.key_hint)
        online_layout.addLayout(form)
        privacy_note = QLabel(
            "仅当选择在线翻译时，识别后的字幕文字才会发送到该接口；在线接口必须使用 HTTPS，"
            "本机 Ollama 除外。系统音频始终只在本机处理。",
            self.openai_group,
        )
        privacy_note.setObjectName("Tiny")
        privacy_note.setWordWrap(True)
        online_layout.addWidget(privacy_note)
        layout.addWidget(self.openai_group)

        self.ollama_group, ollama_layout = self._group("Ollama 本机服务")
        oform2 = self._form(self.ollama_group)
        self.ollama_endpoint = QLineEdit()
        self.ollama_endpoint.setPlaceholderText("http://127.0.0.1:11434/v1")
        self.ollama_model = QLineEdit()
        self.ollama_model.setPlaceholderText("qwen2.5:7b")
        oform2.addRow("服务地址", self.ollama_endpoint)
        oform2.addRow("模型名称", self.ollama_model)
        ollama_layout.addLayout(oform2)
        ollama_note = QLabel(
            "需要先安装 Ollama 并拉取模型。请求只发往本机地址，字幕不会离开这台电脑。", self.ollama_group
        )
        ollama_note.setObjectName("Tiny")
        ollama_note.setWordWrap(True)
        ollama_layout.addWidget(ollama_note)
        layout.addWidget(self.ollama_group)

        self.none_group = QLabel("翻译已关闭。屏幕上只显示英文原声，记录中也不会出现中文。")
        self.none_group.setObjectName("Muted")
        self.none_group.setWordWrap(True)
        self.none_group.setMargin(8)
        layout.addWidget(self.none_group)

        common = Card(page)
        common_layout = QVBoxLayout(common)
        common_layout.setContentsMargins(15, 13, 15, 13)
        common_layout.setSpacing(8)
        cform = self._form(common)
        self.context_spin = QSpinBox()
        self.context_spin.setRange(0, 5)
        self.context_spin.setSuffix(" 句")
        cform.addRow("携带上文", self.context_spin)
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(5.0, 90.0)
        self.timeout_spin.setSingleStep(5.0)
        self.timeout_spin.setSuffix(" 秒")
        cform.addRow("在线超时", self.timeout_spin)
        common_layout.addLayout(cform)
        context_note = QLabel("携带上文可改善代词和术语的连贯性；在线 LLM 与 Qwen 本地 LLM 会使用。", common)
        context_note.setObjectName("Tiny")
        context_note.setWordWrap(True)
        common_layout.addWidget(context_note)
        self.meeting_context_edit = QLineEdit(common)
        self.meeting_context_edit.setPlaceholderText("例如：季度产品评审，涉及支付与风控")
        self.meeting_context_edit.setMaxLength(600)
        common_layout.addWidget(QLabel("会议背景", common))
        common_layout.addWidget(self.meeting_context_edit)
        self.glossary_edit = QPlainTextEdit(common)
        self.glossary_edit.setPlaceholderText(
            "术语对照，每行一个，例如：\nSLA = 服务等级协议\nTom Chen = 陈汤姆"
        )
        self.glossary_edit.setMaximumHeight(78)
        common_layout.addWidget(QLabel("术语表（English = 中文）", common))
        common_layout.addWidget(self.glossary_edit)
        glossary_note = QLabel(
            "术语表会用于 Qwen 本地 LLM / 在线 LLM 的翻译提示；其中的英文术语也会自动作为识别热词。",
            common,
        )
        glossary_note.setObjectName("Tiny")
        glossary_note.setWordWrap(True)
        common_layout.addWidget(glossary_note)
        test_row = QHBoxLayout()
        self.test_button = QPushButton("测试翻译", common)
        self.test_button.setObjectName("SecondaryButton")
        self.test_button.clicked.connect(self._test_translation)
        self.test_result = QLabel("", common)
        self.test_result.setObjectName("Tiny")
        self.test_result.setWordWrap(True)
        self.test_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        common_layout.addLayout(test_row)
        layout.addWidget(common)
        layout.addStretch(1)

        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        self.endpoint_edit.editingFinished.connect(self._refresh_key_hint)
        self.offline_model_combo.currentIndexChanged.connect(self._update_offline_status)
        return page

    def _build_appearance_tab(self) -> QWidget:
        page, layout = self._scroll_page()
        self.preview = OverlayPreview(page)
        layout.addWidget(self.preview)

        box, box_layout = self._group("字体与颜色")
        form = self._form(box)
        self.font_combo = QFontComboBox()
        form.addRow("字体", self.font_combo)
        self.source_size_spin = QSpinBox()
        self.source_size_spin.setRange(10, 40)
        self.source_size_spin.setSuffix(" px")
        self.target_size_spin = QSpinBox()
        self.target_size_spin.setRange(12, 48)
        self.target_size_spin.setSuffix(" px")
        size_row = QWidget()
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.addWidget(QLabel("英文"))
        size_layout.addWidget(self.source_size_spin)
        size_layout.addSpacing(12)
        size_layout.addWidget(QLabel("中文"))
        size_layout.addWidget(self.target_size_spin)
        size_layout.addStretch(1)
        form.addRow("字号", size_row)
        self.source_color_button = ColorButton("#C7D5E8")
        self.target_color_button = ColorButton("#FFFFFF")
        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.addWidget(QLabel("英文"))
        color_layout.addWidget(self.source_color_button)
        color_layout.addSpacing(12)
        color_layout.addWidget(QLabel("中文"))
        color_layout.addWidget(self.target_color_button)
        color_layout.addStretch(1)
        form.addRow("文字颜色", color_row)
        box_layout.addLayout(form)
        layout.addWidget(box)

        box2, box2_layout = self._group("字幕面板")
        form2 = self._form(box2)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_label = QLabel("84%")
        form2.addRow("背景浓度", _slider_row(self.opacity_slider, self.opacity_label))
        self.width_slider = QSlider(Qt.Horizontal)
        self.width_slider.setRange(620, 1800)
        self.width_slider.setSingleStep(20)
        self.width_label = QLabel("1060 px")
        form2.addRow("字幕宽度", _slider_row(self.width_slider, self.width_label))
        self.blocks_spin = QSpinBox()
        self.blocks_spin.setRange(1, 5)
        self.blocks_spin.setSuffix(" 段")
        form2.addRow("保留段数", self.blocks_spin)
        self.show_source_check = QCheckBox("显示英文原声（关闭后只看中文）")
        form2.addRow("", self.show_source_check)
        box2_layout.addLayout(form2)
        layout.addWidget(box2)

        box3, box3_layout = self._group("位置与显示")
        form3 = self._form(box3)
        self.position_combo = QComboBox()
        self.position_combo.addItem("屏幕底部居中（推荐）", "bottom")
        self.position_combo.addItem("屏幕顶部居中", "top")
        self.position_combo.addItem("自由位置 · 直接拖动字幕", "free")
        form3.addRow("初始位置", self.position_combo)
        box3_layout.addLayout(form3)
        pos_hint = QLabel(
            "悬浮字幕始终置顶。直接拖动字幕可自由移动；右键可锁定鼠标穿透、暂停或调整字号。",
            box3,
        )
        pos_hint.setObjectName("Tiny")
        pos_hint.setWordWrap(True)
        box3_layout.addWidget(pos_hint)
        self.click_through_check = QCheckBox("鼠标穿透（锁定位置；需从主窗口或托盘解锁）")
        self.exclude_capture_check = QCheckBox("屏幕共享/录屏时隐藏字幕（Win10 2004+）")
        box3_layout.addWidget(self.click_through_check)
        box3_layout.addWidget(self.exclude_capture_check)
        layout.addWidget(box3)

        reset = QPushButton("恢复默认外观")
        reset.setObjectName("Chip")
        reset.setMaximumWidth(150)
        reset.clicked.connect(self._reset_appearance)
        layout.addWidget(reset, 0, Qt.AlignLeft)
        layout.addStretch(1)

        self.font_combo.currentFontChanged.connect(self._update_preview)
        self.source_size_spin.valueChanged.connect(self._update_preview)
        self.target_size_spin.valueChanged.connect(self._update_preview)
        self.source_color_button.color_changed.connect(self._update_preview)
        self.target_color_button.color_changed.connect(self._update_preview)
        self.opacity_slider.valueChanged.connect(self._update_preview)
        self.width_slider.valueChanged.connect(self._update_preview)
        self.blocks_spin.valueChanged.connect(self._update_preview)
        self.show_source_check.toggled.connect(self._update_preview)
        self.opacity_slider.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        self.width_slider.valueChanged.connect(lambda v: self.width_label.setText(f"{v} px"))
        return page

    def _build_records_tab(self) -> QWidget:
        page, layout = self._scroll_page()
        box, box_layout = self._group("字幕记录")
        form = self._form(box)
        self.autosave_check = QCheckBox("开始字幕后自动保存 Markdown 与 JSONL 记录")
        form.addRow("", self.autosave_check)
        self.timestamp_check = QCheckBox("在记录中显示每段字幕的时间")
        form.addRow("", self.timestamp_check)
        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("记录保存目录")
        browse = QPushButton("选择…")
        browse.setObjectName("Chip")
        browse.clicked.connect(self._choose_dir)
        open_button = QPushButton("打开")
        open_button.setObjectName("Chip")
        open_button.clicked.connect(lambda: open_directory(Path(self.dir_edit.text() or str(Path.home()))))
        dir_row = QWidget()
        dir_layout = QHBoxLayout(dir_row)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.addWidget(self.dir_edit, 1)
        dir_layout.addWidget(browse)
        dir_layout.addWidget(open_button)
        form.addRow("保存目录", dir_row)
        box_layout.addLayout(form)
        note = QLabel(
            "记录只保存在本机。Markdown 方便阅读，JSONL 保留结构化字幕与时间信息。关闭自动保存后，可随时在托盘或主窗口手动保存。",
            box,
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        box_layout.addWidget(note)
        layout.addWidget(box)

        behavior, behavior_layout = self._group("启动与窗口")
        behavior_form = self._form(behavior)
        self.close_tray_check = QCheckBox("关闭主窗口时最小化到系统托盘")
        self.autostart_check = QCheckBox("启动应用后自动开始字幕")
        behavior_form.addRow("", self.close_tray_check)
        behavior_form.addRow("", self.autostart_check)
        behavior_layout.addLayout(behavior_form)
        behavior_note = QLabel(
            "自动开始会在应用启动时立即采集系统播放的声音；在共享电脑上建议保持关闭。",
            behavior,
        )
        behavior_note.setObjectName("Tiny")
        behavior_note.setWordWrap(True)
        behavior_layout.addWidget(behavior_note)
        layout.addWidget(behavior)

        privacy = Card(page)
        p_layout = QVBoxLayout(privacy)
        p_layout.setContentsMargins(16, 14, 16, 14)
        p_layout.setSpacing(7)
        p_title = QLabel("隐私设计", privacy)
        p_title.setStyleSheet("font-weight: 600; font-size: 14px;")
        p_layout.addWidget(p_title)
        bullets = [
            "系统音频仅在本机由 faster-whisper 识别，绝不上传音频。",
            "默认离线翻译：字幕文字也不离开电脑。",
            "可选在线翻译只发送字幕文字，使用你配置的接口；不发送音频。",
            "API 密钥用 Windows DPAPI 加密，只有当前 Windows 用户能解密。",
            "字幕窗口可标记为「屏幕共享时隐藏」；字幕记录为本地明文文件。",
            "无遥测、无账号、无广告；日志不记录会议内容。",
        ]
        for text in bullets:
            label = QLabel("•  " + text, privacy)
            label.setObjectName("Muted")
            label.setWordWrap(True)
            p_layout.addWidget(label)
        layout.addWidget(privacy)
        buttons = QHBoxLayout()
        data_button = QPushButton("打开数据目录")
        data_button.setObjectName("Chip")
        data_button.clicked.connect(lambda: open_directory(data_dir()))
        log_button = QPushButton("打开日志目录")
        log_button.setObjectName("Chip")
        log_button.clicked.connect(lambda: open_directory(log_dir()))
        buttons.addWidget(data_button)
        buttons.addWidget(log_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _build_about_tab(self) -> QWidget:
        page, layout = self._scroll_page()
        head = Card(page)
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(18, 18, 18, 18)
        head_layout.setSpacing(15)
        icon = QLabel(head)
        icon.setPixmap(app_icon_pixmap(128).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon.setFixedSize(66, 66)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel("会译字幕 · MeetSub", head)
        name.setStyleSheet("font-size: 19px; font-weight: 700;")
        desc = QLabel("为国际线上会议设计的本地实时英中字幕工具。", head)
        desc.setObjectName("Muted")
        version = QLabel(f"版本 {__version__} · Windows 10/11", head)
        version.setObjectName("Tiny")
        texts.addWidget(name)
        texts.addWidget(desc)
        texts.addWidget(version)
        head_layout.addWidget(icon)
        head_layout.addLayout(texts, 1)
        layout.addWidget(head)

        box, box_layout = self._group("技术栈")
        info = QLabel(
            "音频捕获：WASAPI loopback（PyAudioWPatch）\n"
            "语音识别：faster-whisper + CTranslate2（本地 CPU 推理）\n"
            "翻译：Qwen2.5 本地 LLM / M2M100 / OPUS-MT，或 OpenAI 兼容接口 / Ollama\n"
            "界面：PySide6 / Qt 6，Windows 原生置顶与防录屏标记",
            box,
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        box_layout.addWidget(info)
        layout.addWidget(box)

        box2, box2_layout = self._group("模型与许可")
        license_label = QLabel(
            "faster-whisper / Whisper：MIT；Qwen2.5：Apache-2.0；"
            "M2M100：MIT；OPUS-MT：CC-BY-4.0。"
            "模型权重由用户按需下载，应用本身不收取任何费用。"
            "商用前请自行确认所选模型及第三方组件的许可条款。",
            box2,
        )
        license_label.setObjectName("Tiny")
        license_label.setWordWrap(True)
        box2_layout.addWidget(license_label)
        layout.addWidget(box2)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------- values
    def _load_values(self) -> None:
        self._loading_values = True
        cfg = self.config
        self._reload_devices(cfg.audio.device_name)
        index = self.vad_combo.findData(cfg.audio.vad_aggressiveness)
        self.vad_combo.setCurrentIndex(max(0, index))
        self.silence_slider.setValue(cfg.audio.silence_ms)
        self.max_segment_spin.setValue(cfg.audio.max_segment_s)
        model_index = self.model_combo.findData(cfg.asr.model)
        self.model_combo.setCurrentIndex(max(0, model_index))
        self.threads_spin.setValue(cfg.asr.cpu_threads)
        self.hotwords_edit.setText(cfg.asr.hotwords)
        compute_index = self.compute_combo.findData(cfg.asr.compute_type)
        self.compute_combo.setCurrentIndex(max(0, compute_index))
        if self.model_source_combo.findData(cfg.asr.model_endpoint) < 0:
            self.model_source_combo.addItem(cfg.asr.model_endpoint, cfg.asr.model_endpoint)
        source_index = self.model_source_combo.findData(cfg.asr.model_endpoint)
        if source_index >= 0:
            self.model_source_combo.setCurrentIndex(source_index)

        provider_index = self.provider_combo.findData(cfg.translation.provider)
        self.provider_combo.setCurrentIndex(max(0, provider_index))
        offline_index = self.offline_model_combo.findData(cfg.translation.offline_model)
        self.offline_model_combo.setCurrentIndex(max(0, offline_index))
        self.endpoint_edit.setText(cfg.translation.endpoint)
        self.model_edit.setText(cfg.translation.model)
        if cfg.translation.provider == "ollama":
            self.ollama_endpoint.setText(cfg.translation.endpoint)
            self.ollama_model.setText(cfg.translation.model)
        else:
            self.ollama_endpoint.setText("http://127.0.0.1:11434/v1")
            self.ollama_model.setText("qwen2.5:7b")
        if cfg.translation.provider == "openai":
            endpoint = cfg.translation.endpoint
            if endpoint.startswith("https://api.deepseek.com"):
                self.preset_combo.setCurrentIndex(0)
            elif endpoint.startswith("https://api.openai.com"):
                self.preset_combo.setCurrentIndex(1)
            else:
                self.preset_combo.setCurrentIndex(2)
        self.context_spin.setValue(cfg.translation.context_segments)
        self.timeout_spin.setValue(cfg.translation.timeout_s)
        self.meeting_context_edit.setText(cfg.translation.meeting_context)
        self.glossary_edit.setPlainText(cfg.translation.glossary)
        self._refresh_key_hint()
        self._provider_changed()

        self.font_combo.setCurrentFont(QFont(cfg.appearance.font_family))
        self.source_size_spin.setValue(cfg.appearance.source_font_size)
        self.target_size_spin.setValue(cfg.appearance.target_font_size)
        self.source_color_button.set_color(cfg.appearance.source_color)
        self.target_color_button.set_color(cfg.appearance.target_color)
        self.opacity_slider.setValue(cfg.appearance.background_opacity)
        self.width_slider.setValue(cfg.appearance.panel_width)
        self.blocks_spin.setValue(cfg.appearance.max_blocks)
        self.show_source_check.setChecked(cfg.appearance.show_source)
        position_index = self.position_combo.findData(cfg.appearance.position_mode)
        self.position_combo.setCurrentIndex(max(0, position_index))
        self.click_through_check.setChecked(cfg.appearance.click_through)
        self.exclude_capture_check.setChecked(cfg.appearance.exclude_from_capture)

        self.autosave_check.setChecked(cfg.recording.auto_save)
        self.timestamp_check.setChecked(cfg.recording.include_timestamps)
        self.dir_edit.setText(cfg.recording.directory)
        self.close_tray_check.setChecked(cfg.close_to_tray)
        self.autostart_check.setChecked(cfg.auto_start)
        self._update_preview()
        self._update_model_status()
        self._update_offline_status()
        self._loading_values = False

    def _reload_devices(self, preferred: str | None = None) -> None:
        if preferred is None:
            preferred = self.device_combo.currentData() or self.config.audio.device_name
        self.device_combo.clear()
        self.device_combo.addItem("自动 · 跟随系统默认输出设备", "")
        try:
            endpoints = list_loopback_endpoints()
        except AudioCaptureError as exc:
            self.device_combo.addItem(f"无法枚举音频设备：{exc}", preferred or "")
            return
        found = False
        for endpoint in endpoints:
            suffix = "（系统默认）" if endpoint.is_default else ""
            self.device_combo.addItem(f"{endpoint.display_name}{suffix}", endpoint.name)
            if endpoint.name == preferred:
                found = True
        if preferred and not found:
            self.device_combo.addItem(f"上次选择（当前不在线）：{preferred}", preferred)
        if preferred:
            index = self.device_combo.findData(preferred)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)

    def _provider_changed(self) -> None:
        provider = self.provider_combo.currentData()
        self.offline_group.setVisible(provider == "offline")
        self.openai_group.setVisible(provider == "openai")
        self.ollama_group.setVisible(provider == "ollama")
        self.none_group.setVisible(provider == "none")
        if self._loading_values:
            self._refresh_key_hint()
            return
        if provider == "openai" and not self.endpoint_edit.text().strip():
            self.endpoint_edit.setText("https://api.deepseek.com/v1")
            self.model_edit.setText("deepseek-chat")
        elif provider == "ollama" and self.endpoint_edit.text().strip() in (
            "",
            "https://api.deepseek.com/v1",
        ):
            self.ollama_endpoint.setText("http://127.0.0.1:11434/v1")
            self.ollama_model.setText("qwen2.5:7b")
        self._refresh_key_hint()

    def _preset_changed(self) -> None:
        if self._loading_values:
            return
        preset = self.preset_combo.currentData()
        if preset == "deepseek":
            self.endpoint_edit.setText("https://api.deepseek.com/v1")
            self.model_edit.setText("deepseek-chat")
        elif preset == "openai":
            self.endpoint_edit.setText("https://api.openai.com/v1")
            self.model_edit.setText("gpt-4o-mini")
        self._refresh_key_hint()

    def _refresh_key_hint(self) -> None:
        endpoint = self.endpoint_edit.text().strip()
        has_key = bool(endpoint and self.vault.has_api_key(endpoint))
        self.key_edit.setPlaceholderText(
            "已安全保存 · 留空保持不变" if has_key and not self._key_cleared else "sk-…"
        )
        if self._key_cleared:
            self.key_hint.setText("保存设置后将清除该接口地址已保存的密钥。")
        elif has_key:
            self.key_hint.setText("已保存密钥（Windows DPAPI 加密，仅当前用户可解密）。")
        else:
            self.key_hint.setText("未配置密钥。在线翻译需要 API Key；本地离线翻译不需要。")

    def _clear_key(self) -> None:
        self._key_cleared = True
        self.key_edit.clear()
        self._refresh_key_hint()

    def _update_model_status(self) -> None:
        model = self.model_combo.currentData()
        spec = asr_spec(model)
        ready = spec_ready(spec)
        self.model_status.setText("已就绪，开始字幕时直接加载。" if ready else "尚未下载，开始前需要准备模型。")
        self.model_status.setStyleSheet(
            f"color: {COLORS['green'] if ready else COLORS['amber']}; background: transparent;"
        )
        self.model_detail_label.setText(
            f"模型 {spec.short_label} · {spec.size_label} · 当前模型目录：{spec.name}"
        )

    def _update_offline_status(self) -> None:
        key = self.offline_model_combo.currentData()
        spec = translation_spec(key)
        ready = spec_ready(spec)
        self.offline_status.setText(
            f"已就绪（{spec.size_label}）。" if ready else f"尚未下载（{spec.size_label}）。"
        )
        self.offline_status.setStyleSheet(
            f"color: {COLORS['green'] if ready else COLORS['amber']}; background: transparent;"
        )
        if key == "qwen2.5-1.5b":
            detail = (
                "Qwen2.5-1.5B 本地 LLM 能利用会议背景、术语表和上下文，翻译质量明显高于传统 NMT；"
                "约占 1.5 GB 内存，建议 16 GB 内存的电脑使用。"
            )
        elif key == "m2m100-418m":
            detail = "M2M100-418M（MIT）是平衡选择，约占 0.5 GB 内存；不支持术语表和上下文强约束。"
        else:
            detail = "OPUS-MT 约 150 MB，适合低配置电脑；建议同时把识别模型换成 base.en 或 tiny.en。"
        self.offline_detail.setText(detail)

    def _open_model_setup(self) -> None:
        temp = copy.deepcopy(self.config)
        temp.asr.model = self.model_combo.currentData()
        temp.asr.model_endpoint = self.model_source_combo.currentData()
        temp.translation.provider = "none"
        dialog = ModelSetupDialog(temp, self)
        if dialog.exec() == QDialog.Accepted:
            self._set_model_source(dialog.selected_endpoint())
            self._update_model_status()

    def _open_translation_model_setup(self) -> None:
        temp = copy.deepcopy(self.config)
        temp.translation.provider = "offline"
        temp.translation.offline_model = self.offline_model_combo.currentData()
        dialog = ModelSetupDialog(temp, self)
        if dialog.exec() == QDialog.Accepted:
            self._set_model_source(dialog.selected_endpoint())
            self._update_offline_status()

    def _set_model_source(self, endpoint: str) -> None:
        self.config.asr.model_endpoint = endpoint
        index = self.model_source_combo.findData(endpoint)
        if index < 0:
            self.model_source_combo.addItem(endpoint, endpoint)
            index = self.model_source_combo.count() - 1
        self.model_source_combo.setCurrentIndex(index)

    # ------------------------------------------------------------------ preview
    def _appearance_from_widgets(self) -> AppearanceConfig:
        cfg = AppearanceConfig(**self.config.appearance.__dict__)
        cfg.font_family = self.font_combo.currentFont().family()
        cfg.source_font_size = self.source_size_spin.value()
        cfg.target_font_size = self.target_size_spin.value()
        cfg.source_color = self.source_color_button.color()
        cfg.target_color = self.target_color_button.color()
        cfg.background_opacity = self.opacity_slider.value()
        cfg.panel_width = self.width_slider.value()
        cfg.max_blocks = self.blocks_spin.value()
        cfg.show_source = self.show_source_check.isChecked()
        cfg.position_mode = self.position_combo.currentData() or "bottom"
        cfg.click_through = self.click_through_check.isChecked()
        cfg.exclude_from_capture = self.exclude_capture_check.isChecked()
        return cfg.normalize()

    def _update_preview(self) -> None:
        self.preview.update_preview(self._appearance_from_widgets())

    def _reset_appearance(self) -> None:
        default = AppearanceConfig()
        self.font_combo.setCurrentFont(QFont(default.font_family))
        self.source_size_spin.setValue(default.source_font_size)
        self.target_size_spin.setValue(default.target_font_size)
        self.source_color_button.set_color(default.source_color)
        self.target_color_button.set_color(default.target_color)
        self.opacity_slider.setValue(default.background_opacity)
        self.width_slider.setValue(default.panel_width)
        self.blocks_spin.setValue(default.max_blocks)
        self.show_source_check.setChecked(default.show_source)
        self.position_combo.setCurrentIndex(max(0, self.position_combo.findData(default.position_mode)))
        self.click_through_check.setChecked(default.click_through)
        self.exclude_capture_check.setChecked(default.exclude_from_capture)
        self._update_preview()

    # --------------------------------------------------------------------- test
    def _translation_config_from_widgets(self) -> AppConfig:
        cfg = copy.deepcopy(self.config)
        cfg.translation.provider = self.provider_combo.currentData()
        cfg.translation.offline_model = self.offline_model_combo.currentData()
        if cfg.translation.provider == "openai":
            cfg.translation.endpoint = self.endpoint_edit.text().strip()
            cfg.translation.model = self.model_edit.text().strip()
        elif cfg.translation.provider == "ollama":
            cfg.translation.endpoint = self.ollama_endpoint.text().strip()
            cfg.translation.model = self.ollama_model.text().strip()
        cfg.translation.context_segments = self.context_spin.value()
        cfg.translation.timeout_s = self.timeout_spin.value()
        cfg.translation.meeting_context = self.meeting_context_edit.text().strip()
        cfg.translation.glossary = self.glossary_edit.toPlainText().strip()
        cfg.normalize()
        return cfg

    def _test_translation(self) -> None:
        provider = self.provider_combo.currentData()
        if provider == "none":
            self.test_result.setText("翻译已关闭。")
            return
        cfg = self._translation_config_from_widgets()
        typed_key = self.key_edit.text().strip()
        if provider == "openai":
            endpoint = cfg.translation.endpoint
            has_key = bool(typed_key or self.vault.has_api_key(endpoint))
            if not has_key:
                self.test_result.setText("请先填写 API 密钥。")
                return
        self.test_button.setEnabled(False)
        self.test_result.setText("正在测试翻译…")
        self._test_cancel.clear()
        self._test_bridge = _TestBridge(self)
        self._test_bridge.ok.connect(self._test_ok)
        self._test_bridge.fail.connect(self._test_failed)

        def emit_ok(text: str) -> None:
            if not self._test_cancel.is_set() and self._test_bridge is not None:
                self._test_bridge.ok.emit(text)

        def emit_fail(message: str) -> None:
            if not self._test_cancel.is_set() and self._test_bridge is not None:
                self._test_bridge.fail.emit(message)

        def run() -> None:
            try:
                if provider == "openai":
                    engine = OpenAICompatibleTranslator(cfg, self.vault, api_key_override=typed_key or None)
                else:
                    engine = create_translator(cfg, self.vault)
                engine.load()
                result = engine.translate("Welcome to today's meeting. We will begin in a few minutes.")
                emit_ok(result or "（空结果）")
            except Exception as exc:
                emit_fail(str(exc))

        self._test_thread = threading.Thread(target=run, name="translation-test", daemon=True)
        self._test_thread.start()

    def _test_ok(self, text: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.setText(f"测试成功：{text}")

    def _test_failed(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.setText(f"测试失败：{message[:300]}")

    # --------------------------------------------------------------------- save
    def _choose_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择字幕记录目录", self.dir_edit.text())
        if chosen:
            self.dir_edit.setText(chosen)

    def _apply_values(self) -> None:
        cfg = self.config
        cfg.audio.device_name = str(self.device_combo.currentData() or "")
        cfg.audio.vad_aggressiveness = int(self.vad_combo.currentData() or 2)
        cfg.audio.silence_ms = self.silence_slider.value()
        cfg.audio.max_segment_s = self.max_segment_spin.value()
        cfg.asr.model = str(self.model_combo.currentData())
        cfg.asr.cpu_threads = self.threads_spin.value()
        cfg.asr.compute_type = str(self.compute_combo.currentData())
        cfg.asr.model_endpoint = str(self.model_source_combo.currentData())
        cfg.asr.hotwords = self.hotwords_edit.text().strip()

        provider = str(self.provider_combo.currentData())
        cfg.translation.provider = provider
        cfg.translation.offline_model = str(self.offline_model_combo.currentData())
        if provider == "openai":
            cfg.translation.endpoint = self.endpoint_edit.text().strip()
            cfg.translation.model = self.model_edit.text().strip()
        elif provider == "ollama":
            cfg.translation.endpoint = self.ollama_endpoint.text().strip()
            cfg.translation.model = self.ollama_model.text().strip()
        cfg.translation.context_segments = self.context_spin.value()
        cfg.translation.timeout_s = self.timeout_spin.value()
        cfg.translation.meeting_context = self.meeting_context_edit.text().strip()
        cfg.translation.glossary = self.glossary_edit.toPlainText().strip()
        cfg.appearance = self._appearance_from_widgets()
        cfg.recording.auto_save = self.autosave_check.isChecked()
        cfg.recording.include_timestamps = self.timestamp_check.isChecked()
        cfg.recording.directory = self.dir_edit.text().strip()
        cfg.close_to_tray = self.close_tray_check.isChecked()
        cfg.auto_start = self.autostart_check.isChecked()
        cfg.normalize()

        if provider == "openai":
            endpoint = cfg.translation.endpoint
            typed = self.key_edit.text().strip()
            if self._key_cleared and not typed:
                self.vault.set_api_key(endpoint, "")
            elif typed:
                self.vault.set_api_key(endpoint, typed)
        elif provider == "ollama":
            pass

    def _save(self) -> None:
        try:
            self._apply_values()
        except Exception as exc:
            QMessageBox.critical(self, "无法保存设置", str(exc))
            return
        if self.config.translation.provider == "openai":
            endpoint = self.config.translation.endpoint
            if not self.vault.has_api_key(endpoint) and not self.key_edit.text().strip():
                QMessageBox.information(
                    self,
                    "尚未配置密钥",
                    "在线翻译尚未配置 API 密钥。保存后仍可启动，但翻译会失败；也可以返回选择「本地离线翻译」。",
                )
        self.accept()

    def done(self, result: int) -> None:
        # Stop late test-result callbacks before Qt destroys the dialog. The test
        # worker is a daemon, so a slow provider cannot keep the UI open.
        self._test_cancel.set()
        self._test_bridge = None
        super().done(result)
