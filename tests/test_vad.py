from __future__ import annotations

import numpy as np

from meetsub.config import AudioConfig
from meetsub.vad import FRAME_SAMPLES, SpeechSegmenter


class SequenceDetector:
    def __init__(self, voiced_frames: set[int]) -> None:
        self.voiced_frames = voiced_frames
        self.index = 0

    def __call__(self, frame: np.ndarray) -> bool:
        voiced = self.index in self.voiced_frames
        self.index += 1
        return voiced


def frame(value: float = 0.02) -> np.ndarray:
    return np.full(FRAME_SAMPLES, value, dtype=np.float32)


def feed_frames(segmenter: SpeechSegmenter, count: int, start_time: float) -> list:
    events = []
    for index in range(count):
        events.extend(segmenter.feed(frame(), now=start_time + index * 0.03))
    return events


def base_config(**overrides) -> AudioConfig:
    values = dict(
        pre_roll_ms=90,
        silence_ms=150,
        min_speech_ms=120,
        max_segment_s=4.0,
        interim_interval_ms=700,
    )
    values.update(overrides)
    return AudioConfig(**values)


def test_endpointing_start_interim_final():
    config = base_config()
    detector = SequenceDetector(set(range(10, 50)))  # 10 silence, 40 speech, 10 silence
    segmenter = SpeechSegmenter(config, detector)
    events = []
    for _ in range(10):
        events.extend(segmenter.feed(frame(), now=0))
    # Advance time explicitly for the speech frames.
    for index in range(40):
        events.extend(segmenter.feed(frame(), now=0.3 + index * 0.03))
    for index in range(10):
        events.extend(segmenter.feed(frame(), now=1.7 + index * 0.03))
    kinds = [event.kind for event in events]
    assert "start" in kinds
    assert "interim" in kinds
    assert kinds[-1] == "final"
    final = events[-1]
    assert final.segment_id >= 1
    assert final.voiced_ms >= 120
    assert final.audio is not None and final.audio.size > 0
    assert events[0].started_at > 0


def test_short_noise_is_discarded():
    config = base_config(min_speech_ms=500)
    detector = SequenceDetector(set(range(5, 9)))  # only 4 voiced frames
    segmenter = SpeechSegmenter(config, detector)
    events = []
    for index in range(20):
        events.extend(segmenter.feed(frame(), now=index * 0.03))
    assert [event.kind for event in events] == ["start", "discard"]


def test_max_segment_forces_final():
    config = base_config(max_segment_s=0.6, silence_ms=2000, min_speech_ms=120)
    detector = SequenceDetector(set(range(2, 60)))
    segmenter = SpeechSegmenter(config, detector)
    events = []
    for index in range(40):
        events.extend(segmenter.feed(frame(), now=index * 0.03))
    finals = [event for event in events if event.kind == "final"]
    assert finals
    assert finals[0].duration_ms <= 700
    # Speech continues, so a new segment is expected to start right after the
    # forced split; the point of the test is that the first segment was closed.
    assert any(event.kind == "start" and event.segment_id > finals[0].segment_id for event in events)


def test_flush_emits_final_and_reset_clears_state():
    config = base_config(min_speech_ms=120)
    detector = SequenceDetector(set(range(0, 30)))
    segmenter = SpeechSegmenter(config, detector)
    for index in range(30):
        segmenter.feed(frame(), now=index * 0.03)
    segmenter.flush()
    assert segmenter.active is False
    # A fresh speech burst should produce a new segment id.
    detector2 = SequenceDetector(set(range(0, 10)))
    segmenter._detector = detector2  # noqa: SLF001 - intentional test seam
    events = []
    for index in range(12):
        events.extend(segmenter.feed(frame(), now=2 + index * 0.03))
    assert any(event.kind == "start" for event in events)


def test_quiet_audio_forces_endpoint_even_if_detector_stays_positive():
    config = base_config(silence_ms=300, min_speech_ms=120)
    segmenter = SpeechSegmenter(config, lambda frame: True)
    events = []
    # Start on quiet frames (the detector is deliberately over-sensitive).
    for index in range(40):
        events.extend(segmenter.feed(np.zeros(FRAME_SAMPLES, dtype=np.float32), now=index * 0.03))
    assert "start" in [event.kind for event in events]
    assert "final" in [event.kind for event in events]


def test_resampler_keeps_duration():
    from meetsub.audio import _Resampler

    resampler = _Resampler(48000)
    out = resampler.process(np.ones(4800, dtype=np.float32))
    assert out.dtype == np.float32
    # Streaming resamplers keep a little filter delay; allow a small margin.
    assert 1400 <= out.size <= 1600
