from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..models import ModelDownloader, ModelSpec, required_specs, spec_ready
from ..paths import writable_model_root
from ..utils import open_directory
from .theme import COLORS, app_icon, set_dark_titlebar
from .widgets import Card


class ModelSetupDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = copy.deepcopy(config)
        self.specs: list[ModelSpec] = required_specs(self.config)
        self.downloader: ModelDownloader | None = None
        self._downloaded = False
        self._closing = False
        self.setWindowTitle("准备离线模型 · 会译字幕")
        self.setWindowIcon(app_icon())
        self.setModal(True)
        self.resize(660, 570)
        self.setMinimumWidth(600)
        self._build()
        self._refresh_status()
        QTimer.singleShot(0, lambda: set_dark_titlebar(self))

    def selected_endpoint(self) -> str:
        data = self.source_combo.currentData()
        return str(data or self.config.asr.model_endpoint)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        title = QLabel("准备离线模型", self)
        title.setStyleSheet("font-size: 21px; font-weight: 700; color: #EAF1FB;")
        subtitle = QLabel(
            "语音识别与离线翻译都在本机运行。模型只需下载一次，不会上传会议音频或字幕。",
            self,
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        rows_card = Card(self)
        rows_layout = QVBoxLayout(rows_card)
        rows_layout.setContentsMargins(16, 14, 16, 14)
        rows_layout.setSpacing(12)
        self._row_status: dict[str, QLabel] = {}
        for index, spec in enumerate(self.specs):
            row = QHBoxLayout()
            row.setSpacing(10)
            icon = QLabel("●", rows_card)
            icon.setStyleSheet(f"color: {COLORS['accent']}; background: transparent;")
            row.addWidget(icon)
            texts = QVBoxLayout()
            texts.setSpacing(1)
            label = QLabel(spec.label, rows_card)
            label.setStyleSheet("font-weight: 600; color: #EAF1FB; background: transparent;")
            detail = QLabel(
                f"{spec.size_label} · 下载后永久可用，可随时在设置中更换。",
                rows_card,
            )
            detail.setObjectName("Tiny")
            detail.setWordWrap(True)
            texts.addWidget(label)
            texts.addWidget(detail)
            row.addLayout(texts, 1)
            status = QLabel("", rows_card)
            status.setObjectName("Pill")
            status.setAlignment(Qt.AlignCenter)
            self._row_status[spec.key] = status
            row.addWidget(status)
            rows_layout.addLayout(row)
            if index < len(self.specs) - 1:
                line = QFrame(rows_card)
                line.setObjectName("Divider")
                line.setFixedHeight(1)
                rows_layout.addWidget(line)
        root.addWidget(rows_card)

        source_row = QHBoxLayout()
        source_label = QLabel("模型下载源", self)
        source_label.setObjectName("Muted")
        self.source_combo = QComboBox(self)
        self.source_combo.addItem("国内镜像 · hf-mirror.com（推荐）", "https://hf-mirror.com")
        self.source_combo.addItem("Hugging Face 官方 · huggingface.co", "https://huggingface.co")
        if self.config.asr.model_endpoint not in (
            "https://hf-mirror.com",
            "https://huggingface.co",
        ):
            self.source_combo.addItem(self.config.asr.model_endpoint, self.config.asr.model_endpoint)
        index = self.source_combo.findData(self.config.asr.model_endpoint)
        if index >= 0:
            self.source_combo.setCurrentIndex(index)
        source_row.addWidget(source_label)
        source_row.addWidget(self.source_combo, 1)
        root.addLayout(source_row)

        folder_row = QHBoxLayout()
        folder_label = QLabel(f"模型目录：{writable_model_root()}", self)
        folder_label.setObjectName("Tiny")
        folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        open_button = QPushButton("打开目录", self)
        open_button.setObjectName("Chip")
        open_button.clicked.connect(lambda: open_directory(writable_model_root()))
        folder_row.addWidget(folder_label, 1)
        folder_row.addWidget(open_button)
        root.addLayout(folder_row)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress_text = QLabel("", self)
        self.progress_text.setObjectName("Tiny")
        self.progress_text.setWordWrap(True)
        self.progress_text.setVisible(False)
        root.addWidget(self.progress)
        root.addWidget(self.progress_text)

        root.addStretch(1)
        actions = QHBoxLayout()
        self.download_button = QPushButton("下载缺失模型", self)
        self.download_button.setObjectName("PrimaryButton")
        self.download_button.clicked.connect(self._start_download)
        self.close_button = QPushButton("稍后再说", self)
        self.close_button.setObjectName("SecondaryButton")
        self.close_button.clicked.connect(self._close_clicked)
        actions.addWidget(self.close_button)
        actions.addStretch(1)
        actions.addWidget(self.download_button)
        root.addLayout(actions)

    def _refresh_status(self) -> None:
        missing: list[ModelSpec] = []
        for spec in self.specs:
            ready = spec_ready(spec)
            status = self._row_status.get(spec.key)
            if status is not None:
                status.setText("已就绪" if ready else "需要下载")
                status.setObjectName("PillGreen" if ready else "PillAmber")
                status.style().unpolish(status)
                status.style().polish(status)
            if not ready:
                missing.append(spec)
        if not missing:
            self.download_button.setText("模型已就绪")
            self.download_button.setEnabled(False)
            self.close_button.setText("完成")
            self.progress.setVisible(False)
            self.progress_text.setVisible(False)
        else:
            total = sum(spec.approx_mb for spec in missing)
            size = f"{total / 1024:.1f} GB" if total >= 1024 else f"{total} MB"
            self.download_button.setText(f"下载缺失模型（约 {size}）")
            self.download_button.setEnabled(True)
            self.close_button.setText("稍后再说")

    def _start_download(self) -> None:
        missing = [spec for spec in self.specs if not spec_ready(spec)]
        if not missing:
            self.accept()
            return
        if self.downloader is not None and self.downloader.running:
            return
        endpoint = self.selected_endpoint()
        self.config.asr.model_endpoint = endpoint
        self.source_combo.setEnabled(False)
        self.download_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.progress_text.setVisible(True)
        self.progress_text.setText("正在连接模型源…")
        if self.downloader is None:
            self.downloader = ModelDownloader(self)
            self.downloader.progress.connect(self._on_progress)
            self.downloader.finished.connect(self._on_finished)
            self.downloader.failed.connect(self._on_failed)
            self.downloader.cancelled.connect(self._on_cancelled)
        self.downloader.start(missing, endpoint)

    def _on_progress(self, percent: int, text: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        if text:
            self.progress_text.setText(text)

    def _on_finished(self, paths: list) -> None:
        self._downloaded = True
        self.progress.setValue(100)
        self.progress_text.setText("下载完成，模型已在本机就绪。")
        self.source_combo.setEnabled(True)
        self.download_button.setEnabled(True)
        self.close_button.setEnabled(True)
        self._refresh_status()
        if not self.download_button.isEnabled():
            self.close_button.setText("完成")
        QMessageBox.information(self, "模型已就绪", "离线模型准备完成，现在可以开始实时字幕。")

    def _on_failed(self, message: str) -> None:
        self.progress_text.setText(f"下载失败：{message}")
        self.source_combo.setEnabled(True)
        self.close_button.setEnabled(True)
        self.download_button.setEnabled(True)
        QMessageBox.warning(
            self,
            "模型下载失败",
            "无法完成下载。请检查网络、代理或防火墙，也可以切换到「Hugging Face 官方」源后重试。\n\n"
            + message[:400],
        )

    def _on_cancelled(self) -> None:
        self.progress_text.setText("下载已取消，可稍后继续。")
        self.source_combo.setEnabled(True)
        self.close_button.setEnabled(True)
        self.download_button.setEnabled(True)

    def _close_clicked(self) -> None:
        self.config.asr.model_endpoint = self.selected_endpoint()
        if all(spec_ready(spec) for spec in self.specs) or self._downloaded:
            self.accept()
        else:
            self.reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.downloader is not None and self.downloader.running:
            answer = QMessageBox.question(
                self,
                "取消下载？",
                "模型仍在下载中。关闭窗口会取消本次下载，未完成的文件下次可以继续。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.downloader.cancel()
            self.downloader.wait(1.5)
        event.accept()
