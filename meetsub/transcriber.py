"""Local English speech recognition with faster-whisper."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import AsrConfig

log = logging.getLogger(__name__)

_HALLUCINATION_PATTERNS = (
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "subtitles by",
    "amara.org",
    "www.",
    "transcription by",
)
_NON_WORD_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_SPACE_RE = re.compile(r"\s+")


class AsrError(RuntimeError):
    pass


@dataclass(frozen=True)
class AsrJob:
    segment_id: int
    audio: np.ndarray
    started_at: float
    kind: str  # interim | final
    duration_ms: int = 0


@dataclass(frozen=True)
class AsrResult:
    segment_id: int
    kind: str
    text: str
    started_at: float
    duration_ms: int
    elapsed_s: float


def clean_transcript(text: str) -> str:
    text = _SPACE_RE.sub(" ", (text or "").strip())
    # Remove adjacent duplicate words common in unstable decoder output.
    text = re.sub(r"\b(\w+)(?:\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)
    if not text:
        return ""
    lowered = text.lower().strip()
    if any(pattern in lowered for pattern in _HALLUCINATION_PATTERNS):
        return ""
    if not _NON_WORD_RE.sub("", text):
        return ""
    # Collapse exact duplicated sentences (a classic Whisper silence artifact).
    parts = re.split(r"(?<=[.!?。！？])\s+", text)
    deduped: list[str] = []
    previous = ""
    for part in parts:
        normalized = part.strip().lower()
        if normalized and normalized == previous:
            continue
        deduped.append(part.strip())
        previous = normalized
    return " ".join(p for p in deduped if p).strip()


class SileroGate:
    """Cheap neural speech-presence gate for final segments.

    WebRTC VAD is intentionally conservative for low latency, but it can let a
    noisy segment through. Silero VAD (the same model used by WhisperLiveKit and
    RealtimeSTT) gives a second opinion on the already-captured final audio.
    """

    def __init__(self) -> None:
        self._model = None
        self._failed = False

    def load(self) -> None:
        if self._model is not None or self._failed:
            return
        try:
            from faster_whisper.vad import get_vad_model

            self._model = get_vad_model()
        except Exception as exc:
            self._failed = True
            log.warning("Silero VAD gate unavailable: %s", exc)

    def speech_seconds(self, audio: np.ndarray) -> float | None:
        if self._model is None:
            return None
        try:
            pad = (-len(audio)) % 512
            if pad:
                audio = np.pad(audio, (0, pad))
            probs = np.asarray(self._model(audio), dtype=np.float32).reshape(-1)
            return float(np.sum(probs >= 0.5) * 512 / 16000.0)
        except Exception as exc:
            log.debug("Silero gate failed: %s", exc)
            return None


class WhisperEngine:
    def __init__(self, config: AsrConfig, model_dir: Path) -> None:
        self.config = config
        self.model_dir = Path(model_dir)
        self._model = None
        self._gate = SileroGate()

    def load(self) -> None:
        if self._model is not None:
            return
        if not self.model_dir.is_dir():
            raise AsrError(f"语音模型目录不存在：{self.model_dir}")
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            raise AsrError(f"无法加载 faster-whisper：{exc}") from exc
        started = time.monotonic()
        try:
            self._model = WhisperModel(
                str(self.model_dir),
                device="cpu",
                compute_type=self.config.compute_type,
                cpu_threads=self.config.cpu_threads,
                num_workers=1,
                local_files_only=True,
            )
        except Exception as exc:
            raise AsrError(f"语音识别模型加载失败：{exc}") from exc
        # Load the tiny Silero ONNX model during startup so the first final
        # segment is not delayed by lazy initialization.
        self._gate.load()
        log.info("Whisper model loaded in %.2fs (%s)", time.monotonic() - started, self.config.model)

    def warmup(self) -> None:
        """Run one short inference so the first real phrase is not cold-start slow."""
        if self._model is None:
            return
        started = time.monotonic()
        try:
            self.transcribe(np.zeros(8000, dtype=np.float32), "interim")
        except Exception as exc:
            log.debug("ASR warmup skipped: %s", exc)
        else:
            log.info("ASR warmed up in %.2fs", time.monotonic() - started)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def transcribe(self, audio: np.ndarray, kind: str = "final") -> str:
        if self._model is None:
            raise AsrError("语音识别模型尚未加载。")
        if audio is None or audio.size == 0:
            return ""
        audio = np.asarray(audio, dtype=np.float32)
        if audio.size < 1600:  # shorter than 100 ms
            return ""
        if kind == "final":
            speech_seconds = self._gate.speech_seconds(audio)
            if speech_seconds is not None and speech_seconds < 0.12:
                log.info("Dropped final segment with no Silero speech (%.2fs)", speech_seconds)
                return ""
        started = time.monotonic()
        try:
            segments, _info = self._model.transcribe(
                audio,
                language="en",
                task="transcribe",
                beam_size=1 if kind == "interim" else max(1, self.config.beam_size),
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                without_timestamps=True,
                word_timestamps=False,
                vad_filter=False,
                initial_prompt=self.config.initial_prompt or None,
                hotwords=self.config.hotwords or None,
                no_speech_threshold=0.62,
                log_prob_threshold=-1.05,
                compression_ratio_threshold=2.5,
                no_repeat_ngram_size=3,
                repetition_penalty=1.03,
            )
            pieces: list[str] = []
            for segment in segments:
                text = (segment.text or "").strip()
                if not text:
                    continue
                no_speech = float(getattr(segment, "no_speech_prob", 0.0) or 0.0)
                avg_logprob = float(getattr(segment, "avg_logprob", 0.0) or 0.0)
                if no_speech > 0.72 and avg_logprob < -0.9:
                    continue
                pieces.append(text)
            result = clean_transcript(" ".join(pieces))
            log.debug(
                "ASR %s %.2fs audio in %.2fs -> %d chars",
                kind,
                audio.size / 16000.0,
                time.monotonic() - started,
                len(result),
            )
            return result
        except Exception as exc:
            raise AsrError(f"语音识别失败：{exc}") from exc


class AsrWorker:
    """Single-threaded ASR consumer; finals jump ahead of scrollback interims."""

    def __init__(
        self,
        engine: WhisperEngine,
        on_result: Callable[[AsrResult], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.engine = engine
        self.on_result = on_result
        self.on_error = on_error
        self._lock = threading.Condition()
        self._finals: deque[AsrJob] = deque()
        self._interim: AsrJob | None = None
        self._busy = False
        self._stopping = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="asr-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        with self._lock:
            self._stopping = True
            self._finals.clear()
            self._interim = None
            self._lock.notify_all()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
        self._thread = None

    def submit_interim(self, job: AsrJob) -> None:
        with self._lock:
            if self._stopping:
                return
            if any(pending.segment_id == job.segment_id for pending in self._finals):
                return
            self._interim = job
            self._lock.notify()

    def submit_final(self, job: AsrJob) -> None:
        with self._lock:
            if self._stopping:
                return
            if self._interim is not None and self._interim.segment_id == job.segment_id:
                self._interim = None
            self._finals.append(job)
            if len(self._finals) > 12:
                self._finals.popleft()
            self._lock.notify()

    def clear(self) -> None:
        with self._lock:
            self._finals.clear()
            self._interim = None
            self._lock.notify_all()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._busy or self._finals or self._interim is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._lock.wait(remaining)
            return True

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._finals) + (1 if self._interim is not None else 0) + (1 if self._busy else 0)

    def _loop(self) -> None:
        while True:
            job: AsrJob | None = None
            with self._lock:
                while not self._stopping and not self._finals and self._interim is None:
                    self._lock.wait()
                if self._stopping:
                    break
                if self._finals:
                    job = self._finals.popleft()
                elif self._interim is not None:
                    job = self._interim
                    self._interim = None
                self._busy = True
            assert job is not None
            started = time.monotonic()
            try:
                text = self.engine.transcribe(job.audio, job.kind)
                result = AsrResult(
                    segment_id=job.segment_id,
                    kind=job.kind,
                    text=text,
                    started_at=job.started_at,
                    duration_ms=job.duration_ms,
                    elapsed_s=time.monotonic() - started,
                )
                self.on_result(result)
            except Exception as exc:
                log.warning("ASR job failed: %s", exc)
                try:
                    self.on_error(str(exc))
                except Exception:
                    log.exception("ASR error callback failed")
            finally:
                with self._lock:
                    self._busy = False
                    self._lock.notify_all()
