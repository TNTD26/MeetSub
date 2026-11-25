"""Local model catalogue, completeness checks and the model downloader."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, Signal

from . import paths
from .config import AppConfig
from .utils import human_bytes

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    name: str
    repo: str
    files: tuple[str, ...]
    required: tuple[str, ...]
    label: str
    short_label: str
    approx_mb: int
    min_sizes: dict[str, int]

    @property
    def size_label(self) -> str:
        return f"约 {self.approx_mb} MB"


ASR_SPECS: dict[str, ModelSpec] = {
    "tiny.en": ModelSpec(
        key="tiny.en",
        name="tiny.en",
        repo="Systran/faster-whisper-tiny.en",
        files=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
        required=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
        label="极速 · tiny.en",
        short_label="tiny.en",
        approx_mb=75,
        min_sizes={"model.bin": 20_000_000},
    ),
    "base.en": ModelSpec(
        key="base.en",
        name="base.en",
        repo="Systran/faster-whisper-base.en",
        files=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
        required=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
        label="轻度 · base.en",
        short_label="base.en",
        approx_mb=145,
        min_sizes={"model.bin": 40_000_000},
    ),
    "small.en": ModelSpec(
        key="small.en",
        name="small.en",
        repo="Systran/faster-whisper-small.en",
        files=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
        required=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
        label="推荐 · small.en",
        short_label="small.en",
        approx_mb=462,
        min_sizes={"model.bin": 80_000_000},
    ),
    "medium.en": ModelSpec(
        key="medium.en",
        name="medium.en",
        repo="Systran/faster-whisper-medium.en",
        files=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
        required=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
        label="高精度 · medium.en",
        short_label="medium.en",
        approx_mb=1500,
        min_sizes={"model.bin": 500_000_000},
    ),
}


TRANSLATION_SPECS: dict[str, ModelSpec] = {
    "opus-mt": ModelSpec(
        key="opus-mt",
        name="opus-mt-en-zh",
        repo="gaudi/opus-mt-en-zh-ctranslate2",
        files=("config.json", "model.bin", "source.spm", "target.spm", "shared_vocabulary.json"),
        required=("config.json", "model.bin", "source.spm", "target.spm"),
        label="轻量离线 · OPUS-MT",
        short_label="OPUS-MT",
        approx_mb=152,
        min_sizes={"model.bin": 50_000_000, "source.spm": 100_000, "target.spm": 100_000},
    ),
    "m2m100-418m": ModelSpec(
        key="m2m100-418m",
        name="m2m100-418m",
        repo="gn64/M2M100_418M_CTranslate2",
        files=("config.json", "model.bin", "sentencepiece.bpe.model", "shared_vocabulary.json"),
        required=("config.json", "model.bin", "sentencepiece.bpe.model", "shared_vocabulary.json"),
        label="增强离线 · M2M100-418M",
        short_label="M2M100",
        approx_mb=470,
        min_sizes={"model.bin": 200_000_000, "sentencepiece.bpe.model": 1_000_000},
    ),
    "qwen2.5-1.5b": ModelSpec(
        key="qwen2.5-1.5b",
        name="qwen2.5-1.5b-ct2-int8",
        repo="jncraton/Qwen2.5-1.5B-Instruct-ct2-int8",
        files=(
            "config.json",
            "generation_config.json",
            "model.bin",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocabulary.json",
        ),
        required=("config.json", "model.bin", "tokenizer.json", "vocabulary.json"),
        label="高精度离线 · Qwen2.5-1.5B（LLM）",
        short_label="Qwen2.5-1.5B",
        approx_mb=1490,
        min_sizes={"model.bin": 700_000_000, "tokenizer.json": 1_000_000, "vocabulary.json": 1_000_000},
    ),
}


def asr_spec(model: str) -> ModelSpec:
    return ASR_SPECS.get(model, ASR_SPECS["small.en"])


def translation_spec(key: str) -> ModelSpec:
    return TRANSLATION_SPECS.get(key, TRANSLATION_SPECS["opus-mt"])


def find_spec_dir(spec: ModelSpec) -> Path | None:
    return paths.find_dir_with_files(spec.name, list(spec.required), spec.min_sizes)


def spec_ready(spec: ModelSpec) -> bool:
    return find_spec_dir(spec) is not None


def asr_ready(model: str) -> bool:
    return spec_ready(asr_spec(model))


def translation_ready(config: AppConfig) -> bool:
    if config.translation.provider != "offline":
        return True
    return spec_ready(translation_spec(config.translation.offline_model))


def required_specs(config: AppConfig) -> list[ModelSpec]:
    specs = [asr_spec(config.asr.model)]
    if config.translation.provider == "offline":
        specs.append(translation_spec(config.translation.offline_model))
    return specs


def missing_required(config: AppConfig) -> list[ModelSpec]:
    return [spec for spec in required_specs(config) if not spec_ready(spec)]


def setup_specs(config: AppConfig) -> list[ModelSpec]:
    """Everything a fully offline first run needs, with duplicates removed."""
    specs = [asr_spec(config.asr.model), translation_spec(config.translation.offline_model)]
    unique: list[ModelSpec] = []
    for spec in specs:
        if spec not in unique:
            unique.append(spec)
    return unique


def install_root() -> Path:
    return paths.writable_model_root()


class ModelDownloader(QObject):
    """Downloads model files on a worker thread with resumable HTTP requests."""

    progress = Signal(int, str)
    finished = Signal(list)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._downloaded = 0
        self._total = 0
        self._last_emit = 0.0
        self._active = False
        self._current_label = "正在准备模型…"

    @property
    def running(self) -> bool:
        return self._active

    def start(self, specs: list[ModelSpec], endpoint: str) -> None:
        if self._active:
            return
        self._cancel.clear()
        self._active = True
        self._thread = threading.Thread(
            target=self._run, args=(list(specs), endpoint.rstrip("/")), daemon=True, name="model-downloader"
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout: float = 3.0) -> None:
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)

    def _emit_progress(self, force: bool = False, text: str | None = None) -> None:
        if text:
            self._current_label = text
        now = time.monotonic()
        if not force and now - self._last_emit < 0.15:
            return
        self._last_emit = now
        pct = int(min(99, (self._downloaded / self._total) * 100)) if self._total > 0 else 0
        suffix = f"  {human_bytes(self._downloaded)} / {human_bytes(self._total)}" if self._total else ""
        self.progress.emit(pct, self._current_label + suffix)

    def _run(self, specs: list[ModelSpec], endpoint: str) -> None:
        started = time.monotonic()
        try:
            tasks: list[tuple[ModelSpec, Path, str]] = []
            root = install_root()
            for spec in specs:
                existing = find_spec_dir(spec)
                if existing is not None:
                    continue
                target = root / spec.name
                target.mkdir(parents=True, exist_ok=True)
                for filename in spec.files:
                    tasks.append((spec, target / filename, filename))
            if not tasks:
                self._active = False
                self.progress.emit(100, "模型已就绪")
                self.finished.emit([str(find_spec_dir(s)) for s in specs if find_spec_dir(s)])
                return

            with httpx.Client(
                timeout=httpx.Timeout(60.0, connect=20.0, read=60.0),
                follow_redirects=True,
                headers={"User-Agent": "MeetSub/1.0 (Windows; local meeting subtitles)"},
            ) as client:
                # Discover sizes up front so the progress bar is meaningful.
                sizes: list[int] = []
                for spec, _path, filename in tasks:
                    sizes.append(self._remote_size(client, self._url(endpoint, spec, filename)))
                estimated = sum(
                    max(size, 0) or max(1, int(spec.approx_mb * 1024 * 1024 / max(1, len(spec.files))))
                    for (spec, _, _), size in zip(tasks, sizes)
                )
                self._total = max(1, estimated)
                self._downloaded = sum(path.stat().st_size for _, path, _ in tasks if path.exists())
                self._emit_progress(force=True)

                for (spec, path, filename), expected in zip(tasks, sizes):
                    if self._cancel.is_set():
                        raise _Cancelled()
                    label = f"{spec.short_label} · {filename}"
                    if path.exists() and (expected <= 0 or path.stat().st_size == expected):
                        continue
                    self._emit_progress(force=True, text=f"正在下载 {label}")
                    self._download_file(
                        client, self._url(endpoint, spec, filename), path, expected, spec, filename
                    )
                self._downloaded = self._total
                self.progress.emit(99, "正在校验模型…")

            missing = [spec for spec in specs if find_spec_dir(spec) is None]
            if missing:
                raise RuntimeError("模型文件不完整，请检查网络后重试。")

            elapsed = time.monotonic() - started
            log.info("Model download finished in %.1fs", elapsed)
            self.progress.emit(100, "模型已就绪")
            self._active = False
            self.finished.emit([str(find_spec_dir(s)) for s in specs if find_spec_dir(s)])
        except _Cancelled:
            self._active = False
            log.info("Model download cancelled")
            self.cancelled.emit()
        except Exception as exc:
            self._active = False
            log.warning("Model download failed: %s", exc)
            self.failed.emit(str(exc))

    @staticmethod
    def _url(endpoint: str, spec: ModelSpec, filename: str) -> str:
        return f"{endpoint}/{spec.repo}/resolve/main/{filename}"

    @staticmethod
    def _remote_size(client: httpx.Client, url: str) -> int:
        try:
            response = client.head(url)
            if response.status_code < 400:
                length = response.headers.get("content-length", "")
                return int(length) if length.isdigit() else 0
        except Exception:
            pass
        return 0

    def _download_file(
        self,
        client: httpx.Client,
        url: str,
        path: Path,
        expected: int,
        label: str,
        filename: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        existing = part.stat().st_size if part.exists() else 0
        if expected > 0 and existing >= expected:
            os.replace(part, path)
            self._downloaded += expected
            return

        last_error: Exception | None = None
        for attempt in range(3):
            if self._cancel.is_set():
                raise _Cancelled()
            try:
                headers = {}
                mode = "wb"
                start_at = 0
                if part.exists() and part.stat().st_size > 0:
                    start_at = part.stat().st_size
                    headers["Range"] = f"bytes={start_at}-"
                    mode = "ab"
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 416 and start_at:
                        os.replace(part, path)
                        actual = path.stat().st_size
                        if expected > 0 and actual != expected:
                            path.unlink(missing_ok=True)
                            raise OSError(f"续传文件不完整（{actual}/{expected} 字节）")
                        return
                    if response.status_code >= 400:
                        raise RuntimeError(f"HTTP {response.status_code}")
                    if start_at and response.status_code != 206:
                        # Server ignored Range: restart this file cleanly.
                        start_at = 0
                        mode = "wb"
                        headers.clear()
                    with part.open(mode) as handle:
                        for chunk in response.iter_bytes(1024 * 256):
                            if self._cancel.is_set():
                                handle.flush()
                                raise _Cancelled()
                            if not chunk:
                                continue
                            handle.write(chunk)
                            self._downloaded += len(chunk)
                            self._emit_progress()
                actual = part.stat().st_size if part.exists() else 0
                if expected > 0 and actual != expected:
                    raise OSError(f"下载不完整（{actual}/{expected} 字节）")
                if actual <= 0:
                    raise OSError("下载的文件为空")
                os.replace(part, path)
                self.progress.emit(
                    min(99, int(self._downloaded / max(1, self._total) * 100)),
                    f"{label} 已完成",
                )
                return
            except _Cancelled:
                raise
            except Exception as exc:
                last_error = exc
                log.warning("Download attempt %s for %s failed: %s", attempt + 1, filename, exc)
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"下载 {filename} 失败：{last_error or '未知错误'}")


class _Cancelled(Exception):
    pass
