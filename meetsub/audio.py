"""WASAPI loopback capture: record what the meeting is playing, not the mic."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .config import AudioConfig

log = logging.getLogger(__name__)

TARGET_RATE = 16_000
FRAME_MS = 30

try:  # Imported lazily by the UI when dependencies are missing.
    import pyaudiowpatch as pyaudio
except Exception as exc:  # pragma: no cover - depends on the host install
    pyaudio = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

try:
    import soxr
except Exception:  # pragma: no cover
    soxr = None  # type: ignore[assignment]


class AudioCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioEndpoint:
    index: int
    name: str
    display_name: str
    sample_rate: int
    channels: int
    is_default: bool = False


def _clean_name(name: str) -> str:
    return name.replace(" [Loopback]", "").strip()


def _require_audio() -> None:
    if pyaudio is None:
        raise AudioCaptureError(
            "音频组件不可用。请运行安装脚本重新安装依赖（pyaudiowpatch）。"
            + (f" 详细信息：{_IMPORT_ERROR}" if _IMPORT_ERROR else "")
        )


def list_loopback_endpoints() -> list[AudioEndpoint]:
    """Enumerate all WASAPI render-loopback endpoints on this machine."""
    _require_audio()
    assert pyaudio is not None
    pa = pyaudio.PyAudio()
    try:
        default_index = -1
        try:
            default_index = int(pa.get_default_wasapi_loopback()["index"])
        except Exception:
            pass
        endpoints: list[AudioEndpoint] = []
        seen: set[int] = set()
        for info in pa.get_loopback_device_info_generator():
            index = int(info["index"])
            if index in seen:
                continue
            seen.add(index)
            channels = int(info.get("maxInputChannels", 0) or 0)
            rate = int(round(float(info.get("defaultSampleRate", 48_000) or 48_000)))
            name = str(info.get("name", f"音频设备 {index}"))
            endpoints.append(
                AudioEndpoint(
                    index=index,
                    name=name,
                    display_name=_clean_name(name),
                    sample_rate=rate,
                    channels=channels,
                    is_default=index == default_index,
                )
            )
        endpoints.sort(key=lambda item: (not item.is_default, item.display_name.lower()))
        return endpoints
    finally:
        pa.terminate()


def resolve_endpoint(
    config: AudioConfig, endpoints: list[AudioEndpoint] | None = None
) -> tuple[AudioEndpoint, str]:
    """Resolve the configured device by name (stable) before falling back to default."""
    endpoints = list_loopback_endpoints() if endpoints is None else endpoints
    if not endpoints:
        raise AudioCaptureError("没有找到可用的系统音频输出设备。请确认扬声器或耳机已连接。")

    preferred = (config.device_name or "").strip()
    if preferred:
        for endpoint in endpoints:
            if endpoint.name == preferred or endpoint.display_name == preferred:
                return endpoint, ""
        return next((e for e in endpoints if e.is_default), endpoints[0]), (
            f"上次选择的「{preferred}」不在线，已自动改用「"
            f"{next((e for e in endpoints if e.is_default), endpoints[0]).display_name}」。"
        )

    for endpoint in endpoints:
        if endpoint.is_default:
            return endpoint, ""
    return endpoints[0], ""


class _Resampler:
    def __init__(self, in_rate: int) -> None:
        self.in_rate = in_rate
        self._stream = None
        if in_rate != TARGET_RATE and soxr is not None:
            self._stream = soxr.ResampleStream(in_rate, TARGET_RATE, 1, dtype="float32", quality="LQ")

    def process(self, mono: np.ndarray, last: bool = False) -> np.ndarray:
        if mono.size == 0:
            return mono
        if self.in_rate == TARGET_RATE:
            return mono.astype(np.float32, copy=False)
        if self._stream is not None:
            return np.asarray(self._stream.resample_chunk(mono, last=last), dtype=np.float32)
        # Fallback for systems without soxr. Speech stays intelligible, but the
        # packaged build always includes soxr for cleaner band-limited audio.
        count = max(1, int(round(mono.size * TARGET_RATE / self.in_rate)))
        positions = np.linspace(0.0, mono.size - 1, count, dtype=np.float64)
        result = np.interp(positions, np.arange(mono.size, dtype=np.float64), mono)
        return result.astype(np.float32)


def _rms_level(mono: np.ndarray) -> float:
    if mono.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
    if rms <= 1e-8:
        return 0.0
    db = 20.0 * math.log10(rms)
    return max(0.0, min(1.0, (db + 58.0) / 58.0))


class LoopbackCapture:
    """Reads the selected render-loopback device and emits 16 kHz mono chunks."""

    def __init__(
        self,
        config: AudioConfig,
        on_chunk: Callable[[np.ndarray], None],
        on_level: Callable[[float], None],
        on_started: Callable[[AudioEndpoint, str], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.config = config
        self.on_chunk = on_chunk
        self.on_level = on_level
        self.on_started = on_started
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.endpoint: AudioEndpoint | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="loopback-capture", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.5) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
        self._thread = None

    def _run(self) -> None:
        _require_audio()
        assert pyaudio is not None
        pa = None
        stream = None
        resampler: _Resampler | None = None
        last_level_emit = 0.0
        failures = 0
        try:
            endpoints = list_loopback_endpoints()
            endpoint, note = resolve_endpoint(self.config, endpoints)
            self.endpoint = endpoint
            pa = pyaudio.PyAudio()
            channels = max(1, min(2, endpoint.channels or 2))
            frames_per_buffer = max(160, int(round(endpoint.sample_rate / 50.0)))
            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=endpoint.sample_rate,
                frames_per_buffer=frames_per_buffer,
                input=True,
                input_device_index=endpoint.index,
            )
            resampler = _Resampler(endpoint.sample_rate)
            self.on_started(endpoint, note)
            while not self._stop.is_set():
                try:
                    raw = stream.read(frames_per_buffer, exception_on_overflow=False)
                    failures = 0
                except Exception as exc:
                    failures += 1
                    if failures >= 3:
                        raise AudioCaptureError(f"读取系统音频失败：{exc}") from exc
                    time.sleep(0.1)
                    continue
                samples = np.frombuffer(raw, dtype=np.float32)
                if samples.size == 0:
                    continue
                usable = (samples.size // channels) * channels
                if usable <= 0:
                    continue
                frames = samples[:usable].reshape(-1, channels)
                mono = frames.mean(axis=1) * float(self.config.gain)
                np.clip(mono, -1.0, 1.0, out=mono)
                level = _rms_level(mono)
                now = time.monotonic()
                if now - last_level_emit >= 0.08:
                    last_level_emit = now
                    self.on_level(level)
                assert resampler is not None
                resampled = resampler.process(mono)
                if resampled.size:
                    self.on_chunk(resampled)
        except Exception as exc:
            if not self._stop.is_set():
                message = str(exc) if isinstance(exc, AudioCaptureError) else f"无法捕获系统音频：{exc}"
                log.warning("Audio capture failed: %s", exc)
                try:
                    self.on_error(message)
                except Exception:
                    log.exception("Audio error callback failed")
        finally:
            self.on_level(0.0)
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass
