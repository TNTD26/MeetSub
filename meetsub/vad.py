"""Voice activity detection and endpointing.

Capture is continuous; this module turns it into speech segments with a short
pre-roll, periodic interim snapshots, and a final snapshot after a pause.  It is
deliberately independent from Qt and from Whisper so it can be unit tested.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .config import AudioConfig

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


class SpeechDetector(Protocol):
    def __call__(self, frame: np.ndarray) -> bool: ...


class WebRtcDetector:
    def __init__(self, aggressiveness: int = 2) -> None:
        self._vad = None
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(int(aggressiveness))
        except Exception as exc:  # pragma: no cover - optional native dependency
            log.warning("WebRTC VAD unavailable, using energy VAD: %s", exc)

    def __call__(self, frame: np.ndarray) -> bool:
        if self._vad is None:
            return _energy_speech(frame)
        pcm = np.clip(frame * 32767.0, -32768, 32767).astype("<i2").tobytes()
        try:
            return bool(self._vad.is_speech(pcm, SAMPLE_RATE))
        except Exception:
            return _energy_speech(frame)


def _energy_speech(frame: np.ndarray) -> bool:
    if frame.size == 0:
        return False
    rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
    db = 20.0 * math.log10(max(rms, 1e-9))
    return db > -42.0


@dataclass(frozen=True)
class VadEvent:
    kind: str  # start | interim | final | discard
    segment_id: int
    audio: np.ndarray | None
    started_at: float
    duration_ms: int
    voiced_ms: int


class SpeechSegmenter:
    def __init__(
        self,
        config: AudioConfig,
        detector: SpeechDetector | Callable[[np.ndarray], bool] | None = None,
    ) -> None:
        self.config = config
        self._detector = detector or WebRtcDetector(config.vad_aggressiveness)
        self._pre_roll_max = max(0, int(round(config.pre_roll_ms / FRAME_MS)))
        self._silence_needed = max(1, int(math.ceil(config.silence_ms / FRAME_MS)))
        self._min_voiced_ms = int(config.min_speech_ms)
        self._max_frames = max(1, int(config.max_segment_s * 1000 / FRAME_MS))
        self._interim_frames = max(1, int(round(config.interim_interval_ms / FRAME_MS)))
        self._carry = np.empty(0, dtype=np.float32)
        self._pre_roll: deque[tuple[np.ndarray, bool]] = deque(maxlen=self._pre_roll_max)
        self._frames: list[np.ndarray] = []
        self._active = False
        self._segment_id = 0
        self._started_at = 0.0
        self._voiced_ms = 0
        self._silence_frames = 0
        self._quiet_frames = 0
        self._last_voice_index = 0
        self._next_interim_frame = max(1, int(round(600 / FRAME_MS)))
        self._voiced_streak = 0

    @property
    def active(self) -> bool:
        return self._active

    def reset(self, reset_ids: bool = False) -> None:
        self._carry = np.empty(0, dtype=np.float32)
        self._pre_roll.clear()
        self._frames.clear()
        self._active = False
        self._voiced_streak = 0
        self._silence_frames = 0
        self._quiet_frames = 0
        self._voiced_ms = 0
        if reset_ids:
            self._segment_id = 0

    def feed(self, samples: np.ndarray, now: float | None = None) -> list[VadEvent]:
        if samples is None or len(samples) == 0:
            return []
        now = time.time() if now is None else now
        if self._carry.size:
            samples = np.concatenate((self._carry, np.asarray(samples, dtype=np.float32)))
        else:
            samples = np.asarray(samples, dtype=np.float32)
        events: list[VadEvent] = []
        offset = 0
        # The sample block may contain many 30 ms frames.  Approximate each
        # frame's wall clock from the end of the block; the difference is well
        # below human perception and only affects transcript timestamps.
        block_start = now - len(samples) / SAMPLE_RATE
        while samples.size - offset >= FRAME_SAMPLES:
            frame = samples[offset : offset + FRAME_SAMPLES].copy()
            offset += FRAME_SAMPLES
            frame_time = block_start + (offset / SAMPLE_RATE)
            events.extend(self._process_frame(frame, frame_time))
        self._carry = samples[offset:].copy() if offset < samples.size else np.empty(0, dtype=np.float32)
        return events

    def flush(self, now: float | None = None) -> list[VadEvent]:
        now = time.time() if now is None else now
        self._carry = np.empty(0, dtype=np.float32)
        if not self._active:
            return []
        return self._finish(now, reason="flush")

    def _process_frame(self, frame: np.ndarray, frame_time: float) -> list[VadEvent]:
        speech = bool(self._detector(frame))
        if not self._active:
            self._pre_roll.append((frame, speech))
            self._voiced_streak = self._voiced_streak + 1 if speech else 0
            if self._voiced_streak < 2:
                return []
            self._active = True
            self._segment_id += 1
            self._frames = [item[0] for item in self._pre_roll]
            self._voiced_ms = sum(FRAME_MS for _, voiced in self._pre_roll if voiced)
            self._silence_frames = 0
            self._quiet_frames = 0
            self._last_voice_index = max(
                (idx for idx, (_, voiced) in enumerate(self._pre_roll) if voiced), default=0
            )
            self._started_at = frame_time - (len(self._frames) - 1) * FRAME_MS / 1000.0
            self._next_interim_frame = max(1, int(round(600 / FRAME_MS)))
            self._voiced_streak = 0
            self._pre_roll.clear()
            return [
                VadEvent(
                    kind="start",
                    segment_id=self._segment_id,
                    audio=None,
                    started_at=self._started_at,
                    duration_ms=len(self._frames) * FRAME_MS,
                    voiced_ms=self._voiced_ms,
                )
            ]

        self._frames.append(frame)
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        if rms < 0.0012:
            self._quiet_frames += 1
        else:
            self._quiet_frames = 0
        if speech:
            self._voiced_ms += FRAME_MS
            self._silence_frames = 0
            self._last_voice_index = len(self._frames) - 1
        else:
            self._silence_frames += 1

        frame_count = len(self._frames)
        duration_ms = frame_count * FRAME_MS
        # WebRTC VAD can stay positive on a quiet but non-zero loopback stream.
        # A truly silent stretch is unambiguous, so also use a low-energy stop.
        if self._silence_frames >= self._silence_needed or self._quiet_frames >= self._silence_needed:
            return self._finish(frame_time, reason="silence")
        if frame_count >= self._max_frames:
            return self._finish(frame_time, reason="max")
        if frame_count >= self._next_interim_frame and duration_ms >= 500:
            self._next_interim_frame = frame_count + self._interim_frames
            return [
                VadEvent(
                    kind="interim",
                    segment_id=self._segment_id,
                    audio=np.concatenate(self._frames),
                    started_at=self._started_at,
                    duration_ms=duration_ms,
                    voiced_ms=self._voiced_ms,
                )
            ]
        return []

    def _finish(self, now: float, reason: str) -> list[VadEvent]:
        if not self._active:
            return []
        segment_id = self._segment_id
        frames = self._frames
        if not frames:
            self.reset()
            return []
        if reason == "silence":
            # Keep a little trailing breathing room but not the full pause.
            tail_pad = max(0, int(round(140 / FRAME_MS)))
            cut = min(len(frames), self._last_voice_index + 1 + tail_pad)
            kept = frames[:cut]
        else:
            kept = frames
        audio = np.concatenate(kept)
        duration_ms = len(kept) * FRAME_MS
        voiced_ms = self._voiced_ms
        discard = voiced_ms < self._min_voiced_ms
        event = VadEvent(
            kind="discard" if discard else "final",
            segment_id=segment_id,
            audio=None if discard else audio,
            started_at=self._started_at,
            duration_ms=duration_ms,
            voiced_ms=voiced_ms,
        )
        self._frames = []
        self._active = False
        self._silence_frames = 0
        self._quiet_frames = 0
        self._voiced_streak = 0
        self._voiced_ms = 0
        self._pre_roll.clear()
        return [event]
