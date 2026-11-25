from __future__ import annotations

import json
from pathlib import Path

from meetsub.config import AppConfig, ConfigStore, SecretsVault


def test_defaults_are_offline_first():
    cfg = AppConfig().normalize()
    assert cfg.translation.provider == "offline"
    assert cfg.translation.offline_model == "qwen2.5-1.5b"
    assert cfg.asr.model == "small.en"
    assert cfg.asr.initial_prompt == ""
    assert cfg.appearance.position_mode == "bottom"
    assert cfg.audio.silence_ms == 650
    assert cfg.auto_start is False


def test_legacy_prompt_is_cleared():
    cfg = AppConfig()
    cfg.asr.initial_prompt = (
        "A professional online business meeting in English. Use clear punctuation, capitalization, "
        "and correct product and people names."
    )
    cfg.normalize()
    assert cfg.asr.initial_prompt == ""


def test_normalization_clamps_and_repairs():
    cfg = AppConfig()
    cfg.appearance.source_font_size = 999
    cfg.appearance.target_font_size = 1
    cfg.appearance.background_opacity = -5
    cfg.appearance.source_color = "red"
    cfg.appearance.position_mode = "elsewhere"
    cfg.audio.silence_ms = 10
    cfg.asr.model = "enormous"
    cfg.translation.provider = "strange"
    cfg.normalize()
    assert cfg.appearance.source_font_size <= 40
    assert cfg.appearance.target_font_size >= cfg.appearance.source_font_size
    assert 20 <= cfg.appearance.background_opacity <= 100
    assert cfg.appearance.source_color.startswith("#")
    assert cfg.appearance.position_mode == "bottom"
    assert cfg.audio.silence_ms >= 250
    assert cfg.asr.model == "small.en"
    assert cfg.translation.provider == "offline"


def test_roundtrip_ignores_unknown_fields(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    cfg = AppConfig()
    cfg.asr.model = "base.en"
    cfg.translation.provider = "openai"
    cfg.translation.endpoint = "http://127.0.0.1:11434/v1"
    cfg.recording.directory = str(tmp_path / "records")
    store.save(cfg)
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["unknown_future_field"] = {"hello": "world"}
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = store.load()
    assert loaded.asr.model == "base.en"
    assert loaded.translation.provider == "openai"
    assert loaded.recording.directory == str(tmp_path / "records")
    assert loaded.appearance.target_font_size >= loaded.appearance.source_font_size


def test_broken_config_falls_back(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text("{ this is not json", encoding="utf-8")
    loaded = store.load()
    assert loaded.translation.provider == "offline"
    assert store.last_error


def test_dpapi_secret_roundtrip_and_removal(tmp_path: Path):
    vault = SecretsVault(tmp_path / "secrets.bin")
    vault.set_api_key("https://example.com/v1/", "sk-local-test")
    assert vault.get_api_key("https://example.com/v1") == "sk-local-test"
    raw = (tmp_path / "secrets.bin").read_bytes()
    assert b"sk-local-test" not in raw  # encrypted at rest
    other = SecretsVault(tmp_path / "secrets.bin")
    assert other.get_api_key("https://example.com/v1") == "sk-local-test"
    vault.set_api_key("https://example.com/v1", "")
    assert not vault.get_api_key("https://example.com/v1")
