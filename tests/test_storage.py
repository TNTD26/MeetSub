from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from meetsub.storage import SessionRecorder


def make_recorder(tmp_path: Path, auto_save: bool = True) -> SessionRecorder:
    return SessionRecorder(
        directory=tmp_path,
        auto_save=auto_save,
        include_timestamps=True,
        asr_label="faster-whisper small.en",
        translator_label="M2M100-418M",
    )


def test_autosave_markdown_and_jsonl(tmp_path: Path):
    recorder = make_recorder(tmp_path)
    started = datetime(2026, 5, 1, 10, 30, 5)
    recorder.add_source(1, "Welcome to the meeting.", started)
    recorder.set_translation(1, "欢迎参加会议。")
    recorder.add_source(2, "Let's review the timeline.", started + timedelta(seconds=8))
    recorder.mark_translation_failed(2)
    info = recorder.finish()
    assert info.path_md and info.path_md.exists()
    assert info.path_jsonl and info.path_jsonl.exists()
    markdown = info.path_md.read_text(encoding="utf-8")
    assert "Welcome to the meeting." in markdown
    assert "欢迎参加会议。" in markdown
    assert "翻译服务不可用" in markdown
    lines = [json.loads(line) for line in info.path_jsonl.read_text(encoding="utf-8").splitlines()]
    assert [entry["translation"] for entry in lines] == ["欢迎参加会议。", ""]
    assert all("source" in entry and "started_at" in entry for entry in lines)


def test_manual_save_when_autosave_off(tmp_path: Path):
    recorder = make_recorder(tmp_path, auto_save=False)
    recorder.add_source(1, "Only kept in memory.", datetime.now())
    recorder.set_translation(1, "只保存在内存。")
    assert not list(tmp_path.glob("*.md"))
    info = recorder.save()
    assert info.path_md and info.path_md.exists()
    assert "只保存在内存。" in info.path_md.read_text(encoding="utf-8")


def test_empty_session_writes_nothing(tmp_path: Path):
    recorder = make_recorder(tmp_path)
    info = recorder.finish()
    assert info.path_md is None
    assert info.path_jsonl is None
    assert not list(tmp_path.iterdir())
