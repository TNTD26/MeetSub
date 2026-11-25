from __future__ import annotations

import copy
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from .config import AppConfig, ConfigStore, SecretsVault
from .models import missing_required
from .pipeline import (
    STATE_IDLE,
    STATE_LISTENING,
    STATE_LOADING,
    STATE_PAUSED,
    STATE_STOPPING,
    CaptionPipeline,
)
from .ui.control import ControlWindow
from .ui.model_setup import ModelSetupDialog
from .ui.overlay import SubtitleOverlay
from .ui.settings import SettingsDialog
from .ui.theme import app_icon, set_dark_titlebar
from .utils import open_directory

log = logging.getLogger(__name__)


class MeetSubApp(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.store = ConfigStore()
        self.config: AppConfig = self.store.load()
        self.vault = SecretsVault()
        self.pipeline = CaptionPipeline(self.config, self.vault)
        self.overlay = SubtitleOverlay(self.config.appearance)
        self.control = ControlWindow()
        self._quitting = False
        self._shutdown_done = False
        self._pending_start = False
        self._error_dialog_open = False
        self._tray_notice_shown = False
        self._first_run_handled = False
        self.tray: QSystemTrayIcon | None = None
        self.tray_menu: QMenu | None = None
        self._setup_tray()
        self._connect_signals()
        self._refresh_all()
        self.control.set_state("idle", "准备就绪。点击开始，自动捕获电脑播放的会议声音。")
        self.control.set_overlay_visible(False)
        self._position_control()
        self.control.show()
        set_dark_titlebar(self.control)
        self.app.aboutToQuit.connect(self._on_about_to_quit)
        QTimer.singleShot(600, self._startup_checks)
        if self.store.last_error:
            QTimer.singleShot(900, lambda: self.control.show_notice(self.store.last_error or "", 9000))
        if self.vault.last_error:
            QTimer.singleShot(1000, lambda: self.control.show_notice(self.vault.last_error or "", 9000))
        log.info("MeetSub started")

    # -------------------------------------------------------------------- setup
    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("System tray is not available")
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("会译字幕 · 就绪")
        menu = QMenu()
        self.tray_show_action = menu.addAction("显示主窗口")
        self.tray_show_action.triggered.connect(self.show_control)
        self.tray_overlay_action = menu.addAction("显示悬浮字幕")
        self.tray_overlay_action.setCheckable(True)
        self.tray_overlay_action.triggered.connect(self._tray_toggle_overlay)
        self.tray_session_action = menu.addAction("开始实时字幕")
        self.tray_session_action.triggered.connect(self._tray_session_action)
        self.tray_save_action = menu.addAction("保存本次记录")
        self.tray_save_action.triggered.connect(self.save_transcript)
        self.tray_records_action = menu.addAction("打开记录文件夹")
        self.tray_records_action.triggered.connect(self.open_records)
        menu.addSeparator()
        settings_action = menu.addAction("设置…")
        settings_action.triggered.connect(self.open_settings)
        menu.addSeparator()
        quit_action = menu.addAction("退出会译字幕")
        quit_action.triggered.connect(lambda: self.quit_app(confirm=True))
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _connect_signals(self) -> None:
        c = self.control
        c.start_requested.connect(self.start_captioning)
        c.pause_requested.connect(self.pipeline.pause)
        c.resume_requested.connect(self.pipeline.resume)
        c.stop_requested.connect(self.pipeline.stop)
        c.overlay_toggle_requested.connect(self.set_overlay_visible)
        c.settings_requested.connect(self.open_settings)
        c.setup_requested.connect(self.open_model_setup)
        c.save_requested.connect(self.save_transcript)
        c.open_records_requested.connect(self.open_records)
        c.hide_requested.connect(self.hide_to_tray)
        c.close_requested.connect(self._on_control_close)

        o = self.overlay
        o.pause_resume_requested.connect(self.toggle_pause)
        o.stop_requested.connect(self.pipeline.stop)
        o.hide_requested.connect(lambda: self.set_overlay_visible(False))
        o.settings_requested.connect(self.open_settings)
        o.geometry_changed.connect(self._on_overlay_geometry)
        o.font_size_changed.connect(self._on_overlay_font)
        o.click_through_changed.connect(self._on_click_through)
        o.privacy_warning.connect(lambda msg: self.control.show_notice(msg, 10000))

        p = self.pipeline
        p.state_changed.connect(self._on_state_changed)
        p.status_changed.connect(self.control.set_status)
        p.level_changed.connect(self.control.set_level)
        p.partial_source.connect(self._on_partial)
        p.partial_cleared.connect(self.overlay.clear_partial)
        p.final_source.connect(self._on_final)
        p.translation_started.connect(self.overlay.translation_started)
        p.translation_partial.connect(self.overlay.update_translation)
        p.translation_done.connect(self._on_translation_done)
        p.translation_failed.connect(self._on_translation_failed)
        p.device_changed.connect(self.control.set_device)
        p.notice.connect(lambda msg: self.control.show_notice(msg, 8000))
        p.fatal_error.connect(self._on_fatal)
        p.session_started.connect(self._on_session_started)
        p.session_finished.connect(self._on_session_finished)
        p.session_saved.connect(self._on_session_saved)
        p.transcript_updated.connect(self._refresh_transcript_actions)

    def _position_control(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.control.move(
            available.x() + max(10, (available.width() - self.control.width()) // 2 - 230),
            available.y() + max(10, (available.height() - self.control.height()) // 2 - 20),
        )

    # ------------------------------------------------------------------ startup
    def _startup_checks(self) -> None:
        first_launch = self.config.first_run
        if first_launch:
            self.config.first_run = False
            self._save_config()
        missing = missing_required(self.config)
        if missing:
            if first_launch and not self._first_run_handled:
                self._first_run_handled = True
                self.open_model_setup()
            return
        if first_launch:
            self.control.show_notice(
                "欢迎使用会译字幕。本地模型已就绪，点击「开始实时字幕」即可。",
                9000,
            )
        if self.config.auto_start:
            QTimer.singleShot(400, self.start_captioning)

    # ---------------------------------------------------------------- UI actions
    def show_control(self) -> None:
        self.control.showNormal()
        self.control.raise_()
        self.control.activateWindow()

    def hide_to_tray(self) -> None:
        if self.tray is None:
            self.control.showMinimized()
            return
        self.control.hide()
        if not self._tray_notice_shown:
            self._tray_notice_shown = True
            self.tray.showMessage(
                "会译字幕仍在运行",
                "字幕与识别继续在后台工作。双击托盘图标可重新打开主窗口。",
                QSystemTrayIcon.Information,
                3500,
            )

    def _on_control_close(self) -> None:
        if self._quitting:
            return
        if self.tray is not None and self.config.close_to_tray:
            self.hide_to_tray()
        else:
            self.quit_app(confirm=True)

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            if self.control.isVisible():
                self.control.raise_()
                self.control.activateWindow()
            else:
                self.show_control()

    def _tray_toggle_overlay(self, checked: bool) -> None:
        self.set_overlay_visible(checked)

    def _tray_session_action(self) -> None:
        state = self.pipeline.state
        if state == STATE_LISTENING:
            self.pipeline.pause()
        elif state == STATE_PAUSED:
            self.pipeline.resume()
        elif state in (STATE_IDLE,):
            self.start_captioning()

    def toggle_pause(self) -> None:
        state = self.pipeline.state
        if state == STATE_LISTENING:
            self.pipeline.pause()
        elif state == STATE_PAUSED:
            self.pipeline.resume()
        elif state == STATE_IDLE:
            self.start_captioning()

    # ------------------------------------------------------------------- models
    def open_model_setup(self) -> None:
        if not missing_required(self.config):
            QMessageBox.information(
                self.control,
                "模型已就绪",
                "当前配置所需的本地模型都已准备好，可以直接开始字幕。",
            )
            return
        dialog = ModelSetupDialog(self.config, self.control)
        accepted = dialog.exec() == QDialog.Accepted
        if accepted:
            self.config.asr.model_endpoint = dialog.selected_endpoint()
            self._save_config()
            self._refresh_all()
        if self._pending_start:
            self._pending_start = False
            if not missing_required(self.config):
                self.start_captioning()

    def _refresh_all(self) -> None:
        has_key = self.vault.has_api_key(self.config.translation.endpoint)
        self.control.refresh_config(self.config, has_api_key=has_key)
        self.overlay.apply_appearance(self.config.appearance)
        self.control.set_overlay_visible(self.overlay.isVisible())
        self._refresh_tray()
        self._refresh_transcript_actions()

    # -------------------------------------------------------------------- start
    def start_captioning(self) -> None:
        if self.pipeline.state in (STATE_LOADING, STATE_LISTENING, STATE_PAUSED, STATE_STOPPING):
            return
        missing = missing_required(self.config)
        if missing:
            self._pending_start = True
            self.open_model_setup()
            return
        if self.config.translation.provider == "openai" and not self.vault.has_api_key(
            self.config.translation.endpoint
        ):
            QMessageBox.information(
                self.control,
                "还差一个翻译密钥",
                "当前选择的是在线翻译，但还没有填写 API 密钥。\n\n"
                "可以到「设置 → 翻译」填写 DeepSeek / OpenAI 密钥，"
                "或者切换到完全离线的本地翻译。",
            )
            self.open_settings()
            return
        self.control.clear_latest()
        self.control.save_button.setEnabled(False)
        self.set_overlay_visible(True)
        self.show_control()
        self.pipeline.start()

    # -------------------------------------------------------------------- overlay
    def set_overlay_visible(self, visible: bool) -> None:
        if visible:
            self.overlay.show_overlay()
        else:
            self.overlay.hide_overlay()
        self.control.set_overlay_visible(bool(visible) and self.overlay.isVisible())
        if self.tray is not None:
            self.tray_overlay_action.setChecked(self.overlay.isVisible())
            self.tray_overlay_action.setText("隐藏悬浮字幕" if self.overlay.isVisible() else "显示悬浮字幕")

    def _on_overlay_geometry(self, x: int, y: int) -> None:
        self.config.appearance.position_mode = "free"
        self.config.appearance.position_x = int(x)
        self.config.appearance.position_y = int(y)
        self._save_config()

    def _on_overlay_font(self, source: int, target: int) -> None:
        self.config.appearance.source_font_size = int(source)
        self.config.appearance.target_font_size = int(target)
        self._save_config()

    def _on_click_through(self, enabled: bool) -> None:
        self.config.appearance.click_through = bool(enabled)
        self._save_config()

    # --------------------------------------------------------------------- state
    def _on_state_changed(self, state: str) -> None:
        self.control.set_state(state)
        self.overlay.set_state(state)
        if state == STATE_IDLE:
            # Keep meeting content off the desktop once a session has ended.
            if self.overlay.isVisible():
                self.overlay.hide_overlay()
            self.control.set_overlay_visible(False)
            self.control.level_meter.set_level(0.0, active=False)
        self._refresh_tray()

    def _on_partial(self, segment_id: int, text: str) -> None:
        self.overlay.set_partial(segment_id, text)
        self.control.set_partial(text)

    def _on_final(self, segment_id: int, text: str, started_at: float) -> None:
        self.overlay.add_source(segment_id, text, self.pipeline.translation_enabled)
        self.control.set_final_source(text)
        self._refresh_transcript_actions()

    def _on_translation_done(self, segment_id: int, text: str) -> None:
        self.overlay.finish_translation(segment_id, text)
        self.control.set_translation(text)
        self._refresh_transcript_actions()

    def _on_translation_failed(self, segment_id: int, message: str) -> None:
        self.overlay.fail_translation(segment_id, message)
        self.control.translation_failed()
        self.control.show_notice(f"翻译失败：{message[:220]}", 9000)

    def _on_fatal(self, message: str) -> None:
        log.warning("Fatal pipeline error: %s", message[:200])
        self.control.show_notice(message, 12000)
        if self._error_dialog_open or self._quitting:
            return
        self.show_control()
        self._error_dialog_open = True
        try:
            QMessageBox.warning(
                self.control,
                "会译字幕需要处理一个问题",
                message + "\n\n如果问题持续出现，请打开「设置」检查音频设备、模型和翻译配置。",
            )
        finally:
            self._error_dialog_open = False
        self.show_control()

    def _on_session_started(self) -> None:
        self.overlay.begin_session(self.pipeline.translation_enabled)
        self.control.clear_latest()
        self.control.save_button.setEnabled(False)
        if not self.overlay.isVisible():
            self.set_overlay_visible(True)
        self._refresh_tray()

    def _on_session_finished(self, path: str) -> None:
        self.control.set_overlay_visible(False)
        if self.overlay.isVisible():
            self.overlay.hide_overlay()
        if path:
            self.control.show_notice(f"本次记录已保存：{Path(path).name}", 10000)
            self.control.setToolTip(f"最近记录：{path}")
        self._refresh_tray()
        self._refresh_transcript_actions()

    def _on_session_saved(self, path: str) -> None:
        self.control.show_notice(f"记录已保存：{Path(path).name}", 9000)

    # ----------------------------------------------------------------- records
    def save_transcript(self) -> None:
        path = self.pipeline.save_current()
        if path is None:
            QMessageBox.information(
                self.control,
                "暂无可保存的字幕",
                "本次还没有完成任何一句字幕。开始字幕后，识别结果会自动保存。",
            )
            return
        self._refresh_transcript_actions()

    def _refresh_transcript_actions(self) -> None:
        info = self.pipeline.transcript_info
        has_entries = bool(info and info.entries)
        self.control.save_button.setEnabled(has_entries)
        if self.tray is not None:
            self.tray_save_action.setEnabled(has_entries)
        if info and info.path_md:
            self.control.setToolTip(f"最近记录：{info.path_md}")

    def open_records(self) -> None:
        directory = Path(self.config.recording.directory)
        try:
            open_directory(directory)
        except Exception as exc:
            self.control.show_notice(f"无法打开记录目录：{exc}", 7000)

    # ------------------------------------------------------------------ settings
    def open_settings(self) -> None:
        if not self.control.isVisible():
            self.show_control()
        dialog = SettingsDialog(self.config, self.vault, self.control)
        if dialog.exec() != QDialog.Accepted:
            self._refresh_all()
            return
        old = copy.deepcopy(self.config)
        self.config = dialog.config
        self._save_config()
        self._refresh_all()
        if self.pipeline.state in (STATE_LISTENING, STATE_PAUSED):
            changed_session = (
                old.audio != self.config.audio
                or old.asr.model != self.config.asr.model
                or old.translation.provider != self.config.translation.provider
                or old.translation.offline_model != self.config.translation.offline_model
                or old.translation.endpoint != self.config.translation.endpoint
                or old.translation.model != self.config.translation.model
            )
            if changed_session:
                self.control.show_notice("音频、识别或翻译设置将在下一次开始字幕时生效。", 9000)
        if self.config.first_run:
            self.config.first_run = False
            self._save_config()

    # -------------------------------------------------------------------- config
    def _save_config(self) -> None:
        try:
            self.store.save(self.config)
        except Exception as exc:
            log.warning("Could not save config: %s", exc)
            self.control.show_notice(f"设置未能保存：{exc}", 8000)

    # --------------------------------------------------------------------- tray
    def _refresh_tray(self) -> None:
        if self.tray is None:
            return
        state = self.pipeline.state
        if state == STATE_LISTENING:
            self.tray.setToolTip("会译字幕 · 正在聆听")
            self.tray_session_action.setText("暂停字幕")
            self.tray_session_action.setEnabled(True)
        elif state == STATE_PAUSED:
            self.tray.setToolTip("会译字幕 · 已暂停")
            self.tray_session_action.setText("继续字幕")
            self.tray_session_action.setEnabled(True)
        elif state == STATE_LOADING:
            self.tray.setToolTip("会译字幕 · 正在准备")
            self.tray_session_action.setText("正在准备…")
            self.tray_session_action.setEnabled(False)
        elif state == STATE_STOPPING:
            self.tray.setToolTip("会译字幕 · 正在保存")
            self.tray_session_action.setText("正在保存…")
            self.tray_session_action.setEnabled(False)
        else:
            self.tray.setToolTip("会译字幕 · 就绪")
            self.tray_session_action.setText("开始实时字幕")
            self.tray_session_action.setEnabled(True)
        visible = self.overlay.isVisible()
        self.tray_overlay_action.setChecked(visible)
        self.tray_overlay_action.setText("隐藏悬浮字幕" if visible else "显示悬浮字幕")

    # --------------------------------------------------------------------- quit
    def quit_app(self, confirm: bool = True) -> None:
        if self._quitting:
            return
        state = self.pipeline.state
        if confirm and state in (STATE_LISTENING, STATE_PAUSED, STATE_LOADING):
            answer = QMessageBox.question(
                self.control,
                "退出会译字幕？",
                "本次会议的实时字幕仍在进行。退出后本次记录会自动保存到本机。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._quitting = True
        self._save_config()
        try:
            self.pipeline.shutdown(timeout=8.0)
        except Exception:
            log.exception("Pipeline shutdown failed")
        self._shutdown_done = True
        if self.tray is not None:
            self.tray.hide()
        self.overlay.hide()
        self.control.hide()
        self.app.quit()

    def _on_about_to_quit(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            self.pipeline.shutdown(timeout=4.0)
        except Exception:
            log.exception("Pipeline shutdown on quit failed")
        self._save_config()
        if self.tray is not None:
            self.tray.hide()
