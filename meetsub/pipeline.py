"""The capture → VAD → ASR → translation → transcript pipeline."""

from __future__ import annotations

import copy
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .audio import LoopbackCapture
from .config import AppConfig, SecretsVault
from .models import asr_spec, find_spec_dir
from .storage import SessionInfo, SessionRecorder
from .transcriber import AsrJob, AsrResult, AsrWorker, WhisperEngine
from .translator import (
    TranslationJob,
    TranslationService,
    create_translator,
    translator_description,
)
from .vad import SpeechSegmenter, VadEvent

log = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_LOADING = "loading"
STATE_LISTENING = "listening"
STATE_PAUSED = "paused"
STATE_STOPPING = "stopping"
STATE_ERROR = "error"


class PipelineStartError(RuntimeError):
    pass


def glossary_to_hotwords(glossary: str, limit: int = 800) -> str:
    """Use the English side of a user glossary as Whisper hotwords.

    The glossary is entered as ``English = 中文`` lines. Only the English side is
    useful for the recognizer; keeping this short avoids prompt echo.
    """
    terms: list[str] = []
    for raw_line in (glossary or "").splitlines():
        line = raw_line.strip().lstrip("-•* ").strip()
        if not line:
            continue
        parts = re.split(r"\s*(?:=>|=|->|:|：)\s*", line, maxsplit=1)
        term = (parts[0] or "").strip(" \t\"'")
        if not term or not re.search(r"[A-Za-z]", term):
            continue
        if term.lower() not in (existing.lower() for existing in terms):
            terms.append(term)
        if len(", ".join(terms)) >= limit:
            break
    return ", ".join(terms)[:limit]


class CaptionPipeline(QObject):
    state_changed = Signal(str)
    status_changed = Signal(str)
    level_changed = Signal(float)
    partial_source = Signal(int, str)
    partial_cleared = Signal(int)
    final_source = Signal(int, str, float)
    translation_started = Signal(int)
    translation_partial = Signal(int, str)
    translation_done = Signal(int, str)
    translation_failed = Signal(int, str)
    device_changed = Signal(str, str)  # display name, fallback note
    notice = Signal(str)
    fatal_error = Signal(str)
    session_started = Signal()
    session_finished = Signal(str)
    session_saved = Signal(str)
    transcript_updated = Signal()
    _internal_fatal = Signal(str)

    def __init__(self, config: AppConfig, vault: SecretsVault, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = copy.deepcopy(config).normalize()
        self.vault = vault
        self._state = STATE_IDLE
        self._status = ""
        self._lock = threading.RLock()
        self._token = 0
        self._startup_thread: threading.Thread | None = None
        self._final_thread: threading.Thread | None = None
        self._capture: LoopbackCapture | None = None
        self._asr: AsrWorker | None = None
        self._translation: TranslationService | None = None
        self._segmenter: SpeechSegmenter | None = None
        self._recorder: SessionRecorder | None = None
        self._translation_enabled = True
        self._active_segment_id: int | None = None
        self._partial_text: dict[int, str] = {}
        self._finished_turn_ids: set[int] = set()
        self._device_name = ""
        self._last_interim_signal: tuple[int, str] | None = None
        self._shutting_down = False
        self._internal_fatal.connect(self._on_internal_fatal)

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def translation_enabled(self) -> bool:
        return self._translation_enabled

    @property
    def transcript_info(self) -> SessionInfo | None:
        with self._lock:
            if self._recorder is None:
                return None
            return SessionInfo(
                started_at=self._recorder.started_at,
                path_md=self._recorder.path_md if self._recorder.path_md.exists() else None,
                path_jsonl=self._recorder.path_jsonl if self._recorder.path_jsonl.exists() else None,
                entries=self._recorder.entry_count,
            )

    def _set_state(self, state: str, status: str | None = None) -> None:
        with self._lock:
            self._state = state
            if status is not None:
                self._status = status
            current_status = self._status
        self.state_changed.emit(state)
        if status is not None:
            self.status_changed.emit(current_status)

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = status
        self.status_changed.emit(status)

    def _is_current(self, token: int) -> bool:
        with self._lock:
            return token == self._token and not self._shutting_down

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        with self._lock:
            if self._state in (STATE_LOADING, STATE_LISTENING, STATE_PAUSED, STATE_STOPPING):
                return
            if self._final_thread is not None and self._final_thread.is_alive():
                return
            if self._recorder is not None and self._recorder.entry_count:
                # A previous session remains available through save_current().
                pass
            self._config.normalize()
            self._token += 1
            token = self._token
            self._shutting_down = False
            self._state = STATE_LOADING
            self._status = "正在准备离线模型…"
            self._partial_text.clear()
            self._finished_turn_ids.clear()
            self._active_segment_id = None
            self._last_interim_signal = None
            self._translation_enabled = self._config.translation.provider != "none"
        self.state_changed.emit(STATE_LOADING)
        self.status_changed.emit("正在准备离线模型…")
        self.level_changed.emit(0.0)
        self._startup_thread = threading.Thread(
            target=self._startup, args=(token,), name="pipeline-startup", daemon=True
        )
        self._startup_thread.start()

    def _startup(self, token: int) -> None:
        prepared_asr: AsrWorker | None = None
        prepared_translation: TranslationService | None = None
        try:
            cfg = copy.deepcopy(self._config)
            if not cfg.asr.hotwords and cfg.translation.glossary:
                cfg.asr.hotwords = glossary_to_hotwords(cfg.translation.glossary)
            spec = asr_spec(cfg.asr.model)
            asr_dir = find_spec_dir(spec)
            if asr_dir is None:
                raise PipelineStartError(
                    f"语音识别模型 {spec.short_label} 尚未下载。请先点击「准备模型」完成下载。"
                )
            if not self._is_current(token):
                return

            self._set_status("正在加载语音识别模型…")
            asr_engine = WhisperEngine(cfg.asr, asr_dir)
            asr_engine.load()
            if not self._is_current(token):
                return

            self._set_status("正在预热语音识别…")
            asr_engine.warmup()
            if not self._is_current(token):
                return

            self._set_status("正在加载翻译引擎…")
            translator = create_translator(cfg, self.vault)
            translator.load()
            translator.warmup()
            if not self._is_current(token):
                return

            self._set_status("正在初始化字幕流水线…")
            prepared_asr = AsrWorker(asr_engine, self._on_asr_result, self._on_asr_error)
            prepared_asr.start()
            if self._translation_enabled:
                prepared_translation = TranslationService(
                    translator,
                    history_size=cfg.translation.context_segments,
                    on_partial=self._on_translation_partial,
                    on_done=self._on_translation_done,
                    on_error=self._on_translation_error,
                )
                prepared_translation.start()

            recorder = SessionRecorder(
                directory=Path(cfg.recording.directory),
                auto_save=cfg.recording.auto_save,
                include_timestamps=cfg.recording.include_timestamps,
                asr_label=f"faster-whisper {cfg.asr.model}",
                translator_label=translator_description(cfg),
            )
            segmenter = SpeechSegmenter(cfg.audio)
            capture = LoopbackCapture(
                cfg.audio,
                on_chunk=self._on_audio_chunk,
                on_level=self._on_level,
                on_started=self._on_capture_started,
                on_error=self._on_capture_error,
            )

            with self._lock:
                if not self._is_current(token):
                    prepared_asr.stop()
                    if prepared_translation:
                        prepared_translation.stop()
                    return
                self._asr = prepared_asr
                self._translation = prepared_translation
                self._recorder = recorder
                self._segmenter = segmenter
                self._capture = capture
                self._state = STATE_LISTENING
                self._status = "正在打开系统音频…"
                self._device_name = ""
            self.session_started.emit()
            self.state_changed.emit(STATE_LISTENING)
            self.status_changed.emit("正在打开系统音频…")
            capture.start()
        except Exception as exc:
            log.warning("Pipeline startup failed: %s", exc)
            if prepared_asr is not None:
                prepared_asr.stop()
            if prepared_translation is not None:
                prepared_translation.stop()
            if self._is_current(token):
                with self._lock:
                    self._asr = None
                    self._translation = None
                    self._recorder = None
                    self._segmenter = None
                    self._capture = None
                    self._state = STATE_ERROR
                    self._status = str(exc)
                self.state_changed.emit(STATE_ERROR)
                self.status_changed.emit(str(exc))
                self.fatal_error.emit(str(exc))

    # ------------------------------------------------------------------ pause
    def pause(self) -> None:
        with self._lock:
            if self._state != STATE_LISTENING:
                return
            self._state = STATE_PAUSED
            self._status = "已暂停 · 系统音频已停止捕获"
            capture = self._capture
            segmenter = self._segmenter
        capture.stop() if capture else None
        if segmenter is not None:
            events = segmenter.flush()
            self._handle_vad_events(events)
            segmenter.reset(reset_ids=False)
        self.level_changed.emit(0.0)
        self.state_changed.emit(STATE_PAUSED)
        self.status_changed.emit("已暂停 · 系统音频已停止捕获")

    def resume(self) -> None:
        with self._lock:
            if self._state != STATE_PAUSED:
                return
            capture = self._capture
            if capture is None:
                return
            self._state = STATE_LISTENING
            self._status = "正在打开系统音频…"
        self.state_changed.emit(STATE_LISTENING)
        self.status_changed.emit("正在打开系统音频…")
        capture.start()

    # ------------------------------------------------------------------- stop
    def stop(self) -> None:
        with self._lock:
            if self._state in (STATE_IDLE, STATE_STOPPING):
                return
            if self._state == STATE_LOADING:
                # The user cannot normally reach this path (the UI disables the
                # button), but shutdown() still needs a safe no-op.
                self._shutting_down = True
                self._token += 1
                self._state = STATE_STOPPING
                capture = None
                segmenter = None
            else:
                self._state = STATE_STOPPING
                self._status = "正在完成最后的识别…"
                capture = self._capture
                segmenter = self._segmenter
            token = self._token
        self.state_changed.emit(STATE_STOPPING)
        self.status_changed.emit(self._status)
        if capture is not None:
            capture.stop()
        if segmenter is not None:
            events = segmenter.flush()
            self._handle_vad_events(events)
            segmenter.reset(reset_ids=False)
        self.level_changed.emit(0.0)
        self._final_thread = threading.Thread(
            target=self._finish_session, args=(token,), name="pipeline-finish", daemon=True
        )
        self._final_thread.start()

    def _finish_session(self, token: int) -> None:
        with self._lock:
            asr = self._asr
            translation = self._translation
            recorder = self._recorder
        try:
            self._set_status("正在完成最后的识别…")
            if asr is not None:
                asr.wait_idle(timeout=8.0)
            if translation is not None:
                self._set_status("正在完成最后的翻译…")
                translation.wait_idle(timeout=10.0)
            if asr is not None:
                asr.stop()
            if translation is not None:
                translation.stop()
            path = ""
            if recorder is not None:
                info = recorder.finish()
                path = str(info.path_md or info.path_jsonl or "")
                if recorder.last_error:
                    self.notice.emit(f"记录未能自动保存：{recorder.last_error}")
            with self._lock:
                self._asr = None
                self._translation = None
                self._capture = None
                self._segmenter = None
                self._state = STATE_IDLE
                self._status = "已保存本次记录" if path else "已停止"
            self.state_changed.emit(STATE_IDLE)
            self.status_changed.emit(self._status)
            self.session_finished.emit(path)
            self.transcript_updated.emit()
        except Exception as exc:
            log.warning("Could not finish session cleanly: %s", exc)
            with self._lock:
                self._state = STATE_IDLE
                self._status = "已停止"
            self.state_changed.emit(STATE_IDLE)
            self.status_changed.emit(self._status)
            self.session_finished.emit("")

    def shutdown(self, timeout: float = 7.0) -> None:
        """Best-effort process-exit cleanup, called from the UI thread."""
        with self._lock:
            self._shutting_down = True
            state = self._state
            capture = self._capture
            asr = self._asr
            translation = self._translation
            recorder = self._recorder
            final_thread = self._final_thread
            startup_thread = self._startup_thread
        if state in (STATE_LISTENING, STATE_PAUSED):
            self.stop()
            final_thread = self._final_thread
        elif state == STATE_LOADING:
            with self._lock:
                self._token += 1
        if capture is not None:
            capture.stop()
        if state == STATE_LOADING and startup_thread is not None:
            startup_thread.join(max(1.0, timeout))
        if final_thread is not None:
            final_thread.join(timeout)
        if asr is not None:
            asr.stop()
        if translation is not None:
            translation.stop()
        if recorder is not None and recorder.entry_count:
            try:
                info = recorder.save(Path(self._config.recording.directory))
                if info.path_md:
                    log.info("Saved transcript during shutdown: %s", info.path_md.name)
            except Exception:
                log.exception("Could not save transcript during shutdown")
        with self._lock:
            if self._state != STATE_IDLE:
                self._state = STATE_IDLE
            self._asr = None
            self._translation = None
            self._capture = None
            self._segmenter = None

    # -------------------------------------------------------------- audio callbacks
    def _on_audio_chunk(self, samples) -> None:
        with self._lock:
            if self._state not in (STATE_LISTENING,):
                return
            segmenter = self._segmenter
        if segmenter is None:
            return
        try:
            events = segmenter.feed(samples)
            self._handle_vad_events(events)
        except Exception as exc:
            log.exception("VAD processing failed")
            self._internal_fatal.emit(f"音频处理失败：{exc}")

    def _on_level(self, level: float) -> None:
        if self.state in (STATE_LISTENING, STATE_LOADING):
            self.level_changed.emit(float(level))

    def _on_capture_started(self, endpoint, note: str) -> None:
        with self._lock:
            self._device_name = endpoint.display_name
            listening = self._state == STATE_LISTENING
            if listening:
                self._status = f"正在聆听 · {endpoint.display_name}"
        if listening:
            self.device_changed.emit(endpoint.display_name, note)
            if note:
                self.notice.emit(note)
            self.status_changed.emit(f"正在聆听 · {endpoint.display_name}")

    def _on_capture_error(self, message: str) -> None:
        self._internal_fatal.emit(message)

    def _on_internal_fatal(self, message: str) -> None:
        # Runs in the Qt main thread.
        with self._lock:
            if self._state in (STATE_IDLE, STATE_STOPPING):
                return
            self._state = STATE_ERROR
            self._status = message
            segmenter = self._segmenter
        if self._capture is not None:
            self._capture.stop()
        # Preserve what was heard before the failure, then tear the session down
        # cleanly so the transcript on disk is never left half-open.
        if segmenter is not None:
            try:
                events = segmenter.flush()
                self._handle_vad_events(events)
            except Exception:
                log.exception("Could not flush audio after a fatal error")
        self.level_changed.emit(0.0)
        self.state_changed.emit(STATE_ERROR)
        self.status_changed.emit(message)
        self.fatal_error.emit(message)
        self.stop()

    # --------------------------------------------------------------- VAD events
    def _handle_vad_events(self, events: list[VadEvent]) -> None:
        with self._lock:
            asr = self._asr
        if asr is None:
            return
        for event in events:
            if event.kind == "start":
                self._active_segment_id = event.segment_id
            elif event.kind == "interim" and event.audio is not None:
                asr.submit_interim(
                    AsrJob(
                        segment_id=event.segment_id,
                        audio=event.audio,
                        started_at=event.started_at,
                        kind="interim",
                        duration_ms=event.duration_ms,
                    )
                )
            elif event.kind == "final" and event.audio is not None:
                asr.submit_final(
                    AsrJob(
                        segment_id=event.segment_id,
                        audio=event.audio,
                        started_at=event.started_at,
                        kind="final",
                        duration_ms=event.duration_ms,
                    )
                )
            elif event.kind == "discard":
                self.partial_cleared.emit(event.segment_id)
                self._partial_text.pop(event.segment_id, None)
                if self._active_segment_id == event.segment_id:
                    self._active_segment_id = None

    # ---------------------------------------------------------------- ASR events
    def _on_asr_result(self, result: AsrResult) -> None:
        if result.kind == "interim":
            if self.state != STATE_LISTENING:
                return
            if result.text:
                previous = self._partial_text.get(result.segment_id)
                if previous != result.text:
                    self._partial_text[result.segment_id] = result.text
                    self.partial_source.emit(result.segment_id, result.text)
            return
        text = result.text.strip()
        if not text:
            self.partial_cleared.emit(result.segment_id)
            self._partial_text.pop(result.segment_id, None)
            return
        if result.segment_id in self._finished_turn_ids:
            return
        with self._lock:
            if self._state not in (STATE_LISTENING, STATE_PAUSED, STATE_STOPPING):
                return
            self._finished_turn_ids.add(result.segment_id)
            self._partial_text.pop(result.segment_id, None)
            if self._active_segment_id == result.segment_id:
                self._active_segment_id = None
            recorder = self._recorder
            translation = self._translation
        started = datetime.fromtimestamp(result.started_at or time.time())
        if recorder is not None:
            recorder.add_source(result.segment_id, text, started, datetime.now())
            self.transcript_updated.emit()
        self.final_source.emit(result.segment_id, text, result.started_at)
        if self._translation_enabled and translation is not None:
            self.translation_started.emit(result.segment_id)
            translation.submit(TranslationJob(segment_id=result.segment_id, text=text))

    def _on_asr_error(self, message: str) -> None:
        self.notice.emit(f"语音识别出现异常：{message}")

    # -------------------------------------------------------- translation events
    def _on_translation_partial(self, segment_id: int, text: str) -> None:
        if text:
            self.translation_partial.emit(segment_id, text)

    def _on_translation_done(self, segment_id: int, text: str) -> None:
        with self._lock:
            recorder = self._recorder
        if recorder is not None:
            recorder.set_translation(segment_id, text, "done")
            self.transcript_updated.emit()
        self.translation_done.emit(segment_id, text)

    def _on_translation_error(self, segment_id: int, message: str) -> None:
        with self._lock:
            recorder = self._recorder
        if recorder is not None:
            recorder.mark_translation_failed(segment_id)
        self.translation_failed.emit(segment_id, message)

    # ---------------------------------------------------------------- transcript
    def save_current(self) -> Path | None:
        with self._lock:
            recorder = self._recorder
            directory = Path(self._config.recording.directory)
        if recorder is None or recorder.entry_count == 0:
            return None
        try:
            info = recorder.save(directory)
        except Exception as exc:
            self.notice.emit(f"保存记录失败：{exc}")
            return None
        path = info.path_md or info.path_jsonl
        if path:
            self.session_saved.emit(str(path))
        return path

    def transcript_directory(self) -> Path:
        return Path(self._config.recording.directory)
