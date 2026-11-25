from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from meetsub.config import AppConfig, SecretsVault
from meetsub.translator import (
    OpenAICompatibleTranslator,
    TranslationError,
    TranslationService,
    _split_for_translation,
    clean_translation,
)


class MockHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []
    fail_auth = False

    def log_message(self, *args):  # noqa: D401
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).calls.append({"path": self.path, "payload": payload})
        if self.headers.get("Authorization") != "Bearer sk-test" or type(self).fail_auth:
            body = b'{"error":{"message":"bad key"}}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ["今天", "我们", "开始", "吧。"]:
                data = json.dumps({"choices": [{"delta": {"content": piece}}]}, ensure_ascii=False)
                self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            body = json.dumps(
                {"choices": [{"message": {"content": "今天开始吧。"}}]}, ensure_ascii=False
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture(scope="module")
def mock_server():
    MockHandler.calls = []
    MockHandler.fail_auth = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def online_config(port: int, stream: bool = True) -> AppConfig:
    cfg = AppConfig()
    cfg.translation.provider = "openai"
    cfg.translation.endpoint = f"http://127.0.0.1:{port}/v1"
    cfg.translation.model = "mock-model"
    cfg.translation.stream = stream
    cfg.translation.context_segments = 2
    cfg.normalize()
    return cfg


def test_clean_translation_removes_labels_and_normalizes_cjk():
    assert clean_translation("翻译：你好,世界.") == "你好，世界。"
    assert clean_translation("```中文\n你好。\n```") == "你好。"
    assert clean_translation('"你好。"') == "你好。"


def test_clean_translation_converts_traditional_to_simplified():
    assert clean_translation("請，我無法聽到你！") == "请，我无法听到你！"


def test_split_long_text():
    text = "First sentence. Second sentence. Third sentence."
    chunks = _split_for_translation(text, 20)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 40 for chunk in chunks)


def test_openai_streaming(mock_server, tmp_path: Path):
    vault = SecretsVault(tmp_path / "secrets.bin")
    cfg = online_config(mock_server.server_port, stream=True)
    engine = OpenAICompatibleTranslator(cfg, vault, api_key_override="sk-test")
    engine.load()
    partials: list[str] = []
    result = engine.translate("Let's begin.", on_partial=partials.append)
    assert result == "今天我们开始吧。"
    assert partials[-1] == "今天我们开始吧。"
    assert MockHandler.calls[-1]["path"] == "/v1/chat/completions"
    assert MockHandler.calls[-1]["payload"]["model"] == "mock-model"


def test_openai_nonstream(mock_server, tmp_path: Path):
    vault = SecretsVault(tmp_path / "secrets.bin")
    cfg = online_config(mock_server.server_port, stream=False)
    engine = OpenAICompatibleTranslator(cfg, vault, api_key_override="sk-test")
    engine.load()
    assert engine.translate("Hello.") == "今天开始吧。"


def test_openai_bad_key(mock_server, tmp_path: Path):
    vault = SecretsVault(tmp_path / "secrets.bin")
    cfg = online_config(mock_server.server_port, stream=False)
    engine = OpenAICompatibleTranslator(cfg, vault, api_key_override="wrong")
    engine.load()
    with pytest.raises(TranslationError, match="401"):
        engine.translate("Hello.")


def test_remote_http_endpoint_is_rejected(tmp_path: Path):
    vault = SecretsVault(tmp_path / "secrets.bin")
    cfg = AppConfig()
    cfg.translation.provider = "openai"
    cfg.translation.endpoint = "http://example.com/v1"
    cfg.translation.model = "mock"
    cfg.normalize()
    engine = OpenAICompatibleTranslator(cfg, vault, api_key_override="sk-test")
    with pytest.raises(TranslationError, match="HTTPS"):
        engine.load()


def test_translation_service_callbacks(tmp_path: Path):
    class Engine:
        def translate(self, text, context=None, on_partial=None):
            on_partial("你好")
            return "你好。"

    results = []
    partials = []
    errors = []
    service = TranslationService(
        Engine(),  # type: ignore[arg-type]
        history_size=2,
        on_partial=lambda sid, text: partials.append((sid, text)),
        on_done=lambda sid, text: results.append((sid, text)),
        on_error=lambda sid, msg: errors.append((sid, msg)),
    )
    service.start()
    from meetsub.translator import TranslationJob

    service.submit(TranslationJob(1, "Hello."))
    service.submit(TranslationJob(2, "Bye."))
    assert service.wait_idle(5)
    service.stop()
    assert results == [(1, "你好。"), (2, "你好。")]
    assert partials
    assert not errors


def test_qwen_prompt_includes_context_and_glossary():
    from meetsub.translator import OfflineQwenTranslator

    engine = OfflineQwenTranslator(
        Path("unused"),
        context_segments=2,
        meeting_context="季度产品评审",
        glossary="launch = 上线",
    )
    prompt = engine._build_prompt(  # noqa: SLF001 - prompt construction is pure
        "We will launch next week.",
        [("The budget is approved.", "预算已批准。")],
    )
    assert "季度产品评审" in engine._static_prefix()  # noqa: SLF001
    assert "launch = 上线" in engine._static_prefix()  # noqa: SLF001
    assert "The budget is approved." in prompt
    assert "<source>" in prompt


@pytest.mark.integration
def test_qwen_offline_if_available():
    import os
    import time

    if os.environ.get("MEETSUB_TEST_MODELS") != "1":
        pytest.skip("set MEETSUB_TEST_MODELS=1 to run the slow offline-model test")
    from meetsub.models import find_spec_dir, translation_spec
    from meetsub.translator import OfflineQwenTranslator

    model_dir = find_spec_dir(translation_spec("qwen2.5-1.5b"))
    if model_dir is None:
        pytest.skip("Qwen model not downloaded")
    engine = OfflineQwenTranslator(
        model_dir,
        threads=4,
        meeting_context="季度产品评审",
        glossary="launch = 上线",
    )
    engine.load()
    started = time.monotonic()
    result = engine.translate("We will launch next week.")
    assert "上线" in result
    assert time.monotonic() - started < 20


@pytest.mark.integration
def test_m2m100_offline_if_available():
    import os

    if os.environ.get("MEETSUB_TEST_MODELS") != "1":
        pytest.skip("set MEETSUB_TEST_MODELS=1 to run the slow offline-model test")
    from meetsub.models import find_spec_dir, translation_spec
    from meetsub.translator import OfflineM2M100Translator

    model_dir = find_spec_dir(translation_spec("m2m100-418m"))
    if model_dir is None:
        pytest.skip("M2M100 model not downloaded")
    engine = OfflineM2M100Translator(model_dir)
    engine.load()
    result = engine.translate("Thank you for joining this meeting.")
    assert "会议" in result
