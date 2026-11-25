"""Local transcript session storage (Markdown + JSONL)."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .utils import atomic_write_text, timestamp_slug

log = logging.getLogger(__name__)


@dataclass
class TranscriptEntry:
    segment_id: int
    started_at: str
    source: str
    translation: str = ""
    status: str = "pending"  # pending | done | failed
    ended_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SessionInfo:
    started_at: datetime
    path_md: Path | None = None
    path_jsonl: Path | None = None
    entries: int = 0


class SessionRecorder:
    def __init__(
        self,
        directory: Path,
        auto_save: bool,
        include_timestamps: bool,
        asr_label: str,
        translator_label: str,
        title: str = "会议实时字幕",
    ) -> None:
        self.directory = Path(directory).expanduser()
        self.auto_save = auto_save
        self.include_timestamps = include_timestamps
        self.asr_label = asr_label
        self.translator_label = translator_label
        self.title = title
        self.started_at = datetime.now()
        self.finished_at: datetime | None = None
        self._entries: dict[int, TranscriptEntry] = {}
        self._order: list[int] = []
        self._lock = threading.RLock()
        slug = timestamp_slug(self.started_at)
        self.path_md = self.directory / f"MeetSub_{slug}.md"
        self.path_jsonl = self.directory / f"MeetSub_{slug}.jsonl"
        self.last_error = ""

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def entries(self) -> list[TranscriptEntry]:
        with self._lock:
            return [self._entries[sid] for sid in self._order]

    def add_source(
        self, segment_id: int, source: str, started_at: datetime, ended_at: datetime | None = None
    ) -> None:
        with self._lock:
            entry = self._entries.get(segment_id)
            if entry is None:
                entry = TranscriptEntry(
                    segment_id=segment_id,
                    started_at=started_at.isoformat(timespec="seconds"),
                    source=source,
                    ended_at=(ended_at or datetime.now()).isoformat(timespec="seconds"),
                )
                self._entries[segment_id] = entry
                self._order.append(segment_id)
            else:
                entry.source = source
                entry.started_at = started_at.isoformat(timespec="seconds")
            self._write_if_enabled()

    def set_translation(self, segment_id: int, translation: str, status: str = "done") -> None:
        with self._lock:
            entry = self._entries.get(segment_id)
            if entry is None:
                return
            entry.translation = translation
            entry.status = status
            self._write_if_enabled()

    def mark_translation_failed(self, segment_id: int) -> None:
        with self._lock:
            entry = self._entries.get(segment_id)
            if entry is None:
                return
            entry.status = "failed"
            self._write_if_enabled()

    def finish(self) -> SessionInfo:
        with self._lock:
            self.finished_at = datetime.now()
            if self.auto_save and self._entries:
                self._write()
        return SessionInfo(
            started_at=self.started_at,
            path_md=self.path_md if self.path_md.exists() else None,
            path_jsonl=self.path_jsonl if self.path_jsonl.exists() else None,
            entries=len(self._entries),
        )

    def save(self, directory: Path | None = None) -> SessionInfo:
        """Force an on-demand export, even when auto-save is disabled."""
        with self._lock:
            if directory is not None:
                new_dir = Path(directory).expanduser()
                if new_dir != self.directory:
                    self.directory = new_dir
                    slug = timestamp_slug(self.started_at)
                    self.path_md = new_dir / f"MeetSub_{slug}.md"
                    self.path_jsonl = new_dir / f"MeetSub_{slug}.jsonl"
            if self.finished_at is None:
                self.finished_at = datetime.now()
            self._write()
        return SessionInfo(
            started_at=self.started_at,
            path_md=self.path_md if self.path_md.exists() else None,
            path_jsonl=self.path_jsonl if self.path_jsonl.exists() else None,
            entries=len(self._entries),
        )

    def _write_if_enabled(self) -> None:
        if not self.auto_save:
            return
        try:
            self._write()
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Could not auto-save transcript: %s", exc)

    def _write(self) -> None:
        if not self._entries:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        markdown = self._render_markdown()
        jsonl = self._render_jsonl()
        atomic_write_text(self.path_md, markdown)
        atomic_write_text(self.path_jsonl, jsonl)
        self.last_error = ""

    def _render_markdown(self) -> str:
        lines: list[str] = [
            f"# {self.title}",
            "",
            f"- 开始时间：{self.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 结束时间：{(self.finished_at or datetime.now()).strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 语音识别：{self.asr_label}（本地处理）",
            f"- 翻译：{self.translator_label}",
            "- 生成工具：MeetSub 会译字幕",
            "",
            "---",
            "",
        ]
        for entry in self.entries:
            started = _format_iso(entry.started_at)
            if self.include_timestamps and started:
                lines.append(f"**{started}**")
                lines.append("")
            lines.append(entry.source.strip())
            lines.append("")
            if entry.status == "done" and entry.translation:
                lines.append(entry.translation.strip())
            elif entry.status == "failed":
                lines.append("*（翻译服务不可用，仅保留英文原文）*")
            else:
                lines.append("*（翻译处理中…）*")
            lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _render_jsonl(self) -> str:
        lines = []
        for entry in self.entries:
            lines.append(json.dumps(entry.to_dict(), ensure_ascii=False))
        return "\n".join(lines) + "\n"


def _format_iso(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%H:%M:%S")
    except ValueError:
        return ""


def write_export(
    entries: Iterable[TranscriptEntry],
    path: Path,
    title: str = "会译字幕记录",
    include_timestamps: bool = True,
) -> Path:
    """Export an existing list of entries; useful for tests and manual export."""
    lines = [f"# {title}", ""]
    for entry in entries:
        stamp = _format_iso(entry.started_at)
        if include_timestamps and stamp:
            lines.extend([f"**{stamp}**", ""])
        lines.extend([entry.source, ""])
        if entry.translation:
            lines.extend([entry.translation, ""])
        lines.extend(["---", ""])
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return path
