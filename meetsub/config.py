from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import paths
from .dpapi import SecretProtectionError, protect, unprotect
from .utils import atomic_write_json, clamp, safe_float, safe_int

log = logging.getLogger(__name__)

TRANSLATION_PROVIDERS = ("offline", "openai", "ollama", "none")
WHISPER_MODELS = ("tiny.en", "base.en", "small.en", "medium.en")
COMPUTE_TYPES = ("int8", "float32")
POSITION_MODES = ("bottom", "top", "free")
_FONT_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-()\u4e00-\u9fff]{1,80}$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _default_threads() -> int:
    logical = os.cpu_count() or 4
    # Small English Whisper is fast enough to use more threads on modern CPUs.
    # Keep a sane cap so the UI and translation worker still get CPU time.
    return max(4, min(12, logical // 3 or 4))


@dataclass
class AudioConfig:
    device_name: str = ""
    device_index: int | None = None
    vad_aggressiveness: int = 2
    silence_ms: int = 650
    min_speech_ms: int = 260
    max_segment_s: float = 12.0
    interim_interval_ms: int = 1200
    pre_roll_ms: int = 300
    gain: float = 1.0


@dataclass
class AsrConfig:
    model: str = "small.en"
    compute_type: str = "int8"
    cpu_threads: int = field(default_factory=_default_threads)
    beam_size: int = 2
    language: str = "en"
    model_endpoint: str = "https://hf-mirror.com"
    # Deliberately empty: Whisper can echo long initial prompts when the audio
    # is noisy or silent. The live pipeline must never leak prompt text into
    # captions. Users can still set short hotwords later if needed.
    initial_prompt: str = ""
    hotwords: str = ""


@dataclass
class TranslationConfig:
    provider: str = "offline"
    offline_model: str = "qwen2.5-1.5b"
    endpoint: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    timeout_s: float = 25.0
    context_segments: int = 2
    stream: bool = True
    temperature: float = 0.2
    target_language: str = "zh-CN"
    meeting_context: str = ""
    glossary: str = ""


@dataclass
class AppearanceConfig:
    font_family: str = "Microsoft YaHei UI"
    source_font_size: int = 16
    target_font_size: int = 23
    source_color: str = "#C7D5E8"
    target_color: str = "#FFFFFF"
    background_color: str = "#080E19"
    background_opacity: int = 84
    panel_width: int = 1060
    corner_radius: int = 16
    max_blocks: int = 3
    show_source: bool = True
    always_on_top: bool = True
    exclude_from_capture: bool = True
    click_through: bool = False
    position_mode: str = "bottom"
    position_x: int | None = None
    position_y: int | None = None
    screen_margin: int = 72

    def normalize(self) -> AppearanceConfig:
        self.font_family = (self.font_family or "Microsoft YaHei UI").strip()
        if not _FONT_NAME_RE.match(self.font_family):
            self.font_family = "Microsoft YaHei UI"
        for attr, low, high in (
            ("source_font_size", 10, 40),
            ("target_font_size", 12, 48),
            ("background_opacity", 20, 100),
            ("panel_width", 520, 2400),
            ("corner_radius", 0, 40),
            ("max_blocks", 1, 6),
            ("screen_margin", 0, 400),
        ):
            setattr(self, attr, int(clamp(safe_int(getattr(self, attr), 1), low, high)))
        self.target_font_size = max(self.target_font_size, self.source_font_size)
        for attr in ("source_color", "target_color", "background_color"):
            value = str(getattr(self, attr))
            setattr(self, attr, value if _COLOR_RE.match(value) else "#FFFFFF")
        if self.position_mode not in POSITION_MODES:
            self.position_mode = "bottom"
        if self.position_x is not None:
            self.position_x = int(clamp(safe_int(self.position_x, 0), -10000, 20000))
        if self.position_y is not None:
            self.position_y = int(clamp(safe_int(self.position_y, 0), -10000, 20000))
        self.click_through = bool(self.click_through)
        self.show_source = bool(self.show_source)
        self.always_on_top = bool(self.always_on_top)
        self.exclude_from_capture = bool(self.exclude_from_capture)
        return self


@dataclass
class RecordingConfig:
    auto_save: bool = True
    directory: str = field(default_factory=lambda: str(paths.default_transcript_dir()))
    include_timestamps: bool = True


@dataclass
class AppConfig:
    schema_version: int = 1
    first_run: bool = True
    close_to_tray: bool = True
    auto_start: bool = False
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    appearance: AppearanceConfig = field(default_factory=AppearanceConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)

    def normalize(self) -> AppConfig:
        self.appearance.normalize()

        au = self.audio
        au.device_name = str(au.device_name or "").strip()[:300]
        au.vad_aggressiveness = int(clamp(safe_int(au.vad_aggressiveness, 2), 0, 3))
        au.silence_ms = int(clamp(safe_int(au.silence_ms, 650), 250, 2000))
        au.min_speech_ms = int(clamp(safe_int(au.min_speech_ms, 260), 120, 1200))
        au.max_segment_s = float(clamp(safe_float(au.max_segment_s, 12.0), 4.0, 30.0))
        au.interim_interval_ms = int(clamp(safe_int(au.interim_interval_ms, 1500), 700, 5000))
        au.pre_roll_ms = int(clamp(safe_int(au.pre_roll_ms, 300), 0, 1000))
        au.gain = float(clamp(safe_float(au.gain, 1.0), 0.25, 4.0))

        asr = self.asr
        if asr.model not in WHISPER_MODELS:
            asr.model = "small.en"
        if asr.compute_type not in COMPUTE_TYPES:
            asr.compute_type = "int8"
        asr.cpu_threads = int(clamp(safe_int(asr.cpu_threads, 4), 1, max(1, os.cpu_count() or 8)))
        asr.beam_size = int(clamp(safe_int(asr.beam_size, 2), 1, 5))
        asr.language = "en"
        asr.model_endpoint = (asr.model_endpoint or "https://hf-mirror.com").strip().rstrip("/")
        if not asr.model_endpoint.startswith(("http://", "https://")):
            asr.model_endpoint = "https://hf-mirror.com"
        asr.initial_prompt = str(asr.initial_prompt or "")[:600]
        if asr.initial_prompt.startswith("A professional online business meeting in English."):
            asr.initial_prompt = ""
        asr.hotwords = str(asr.hotwords or "").strip()[:1000]

        tr = self.translation
        if tr.provider not in TRANSLATION_PROVIDERS:
            tr.provider = "offline"
        if tr.offline_model not in ("opus-mt", "m2m100-418m", "qwen2.5-1.5b"):
            tr.offline_model = "qwen2.5-1.5b"
        tr.endpoint = (tr.endpoint or "").strip().rstrip("/")
        if tr.provider == "openai" and not tr.endpoint:
            tr.endpoint = "https://api.deepseek.com/v1"
        if tr.provider == "ollama" and not tr.endpoint:
            tr.endpoint = "http://127.0.0.1:11434/v1"
        if not tr.endpoint.startswith(("http://", "https://")):
            tr.endpoint = "https://api.deepseek.com/v1"
        tr.model = str(tr.model or "deepseek-chat").strip()[:120]
        tr.timeout_s = float(clamp(safe_float(tr.timeout_s, 25.0), 5.0, 120.0))
        tr.context_segments = int(clamp(safe_int(tr.context_segments, 2), 0, 5))
        tr.temperature = float(clamp(safe_float(tr.temperature, 0.2), 0.0, 1.5))
        tr.stream = bool(tr.stream)
        tr.target_language = "zh-CN"
        tr.meeting_context = str(tr.meeting_context or "").strip()[:600]
        tr.glossary = str(tr.glossary or "").strip()[:4000]

        rec = self.recording
        rec.directory = str(rec.directory or paths.default_transcript_dir()).strip()
        if not rec.directory:
            rec.directory = str(paths.default_transcript_dir())
        rec.auto_save = bool(rec.auto_save)
        rec.include_timestamps = bool(rec.include_timestamps)
        return self


def _from_dict(cls: type, data: Any, defaults: Any = None) -> Any:
    if not isinstance(data, dict):
        return defaults if defaults is not None else cls()
    default_obj = defaults if defaults is not None else cls()
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        default_value = getattr(default_obj, f.name)
        if f.name not in data:
            kwargs[f.name] = default_value
            continue
        value = data[f.name]
        if is_dataclass(default_value):
            kwargs[f.name] = _from_dict(type(default_value), value, default_value)
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.config_path()
        self.last_error: str | None = None

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig().normalize()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("config root is not an object")
            cfg = _from_dict(AppConfig, raw)
            return cfg.normalize()
        except Exception as exc:
            self.last_error = f"配置文件读取失败，已使用默认设置：{exc}"
            log.warning("Could not read config: %s", exc)
            return AppConfig().normalize()

    def save(self, config: AppConfig) -> None:
        config.normalize()
        atomic_write_json(self.path, dataclasses.asdict(config))


class SecretsVault:
    """Optional API keys encrypted with the signed-in Windows user's DPAPI key."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.secrets_path()
        self._cache: dict[str, str] | None = None
        self.last_error: str | None = None

    @staticmethod
    def normalize_endpoint(endpoint: str) -> str:
        return (endpoint or "").strip().rstrip("/").lower()

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        values: dict[str, str] = {}
        if self.path.exists():
            try:
                payload = unprotect(self.path.read_bytes())
                decoded = json.loads(payload.decode("utf-8"))
                if isinstance(decoded, dict):
                    values = {str(k): str(v) for k, v in decoded.items() if v}
            except (OSError, SecretProtectionError, ValueError) as exc:
                self.last_error = f"无法读取已保存的密钥：{exc}"
                log.warning("Could not read secrets vault: %s", exc)
        self._cache = values
        return values

    def _persist(self, values: dict[str, str]) -> None:
        payload = json.dumps(values, ensure_ascii=False).encode("utf-8")
        try:
            blob = protect(payload, "MeetSub API key")
        except SecretProtectionError as exc:
            raise RuntimeError("无法使用 Windows DPAPI 加密密钥，密钥未保存。") from exc
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="secrets.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        self._cache = dict(values)

    def get_api_key(self, endpoint: str) -> str:
        env_key = os.environ.get("MEETSUB_API_KEY", "").strip()
        if env_key:
            return env_key
        try:
            return self._load().get(self.normalize_endpoint(endpoint), "")
        except Exception:
            return ""

    def has_api_key(self, endpoint: str) -> bool:
        return bool(self.get_api_key(endpoint))

    def set_api_key(self, endpoint: str, key: str) -> None:
        normalized = self.normalize_endpoint(endpoint)
        values = dict(self._load())
        key = key.strip()
        if key:
            values[normalized] = key
        else:
            values.pop(normalized, None)
        self._persist(values)

    def clear(self) -> None:
        self._persist({})
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass


def load_config() -> tuple[AppConfig, ConfigStore]:
    store = ConfigStore()
    return store.load(), store
