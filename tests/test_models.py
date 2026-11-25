from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from meetsub import models as models_module
from meetsub.models import ModelDownloader, ModelSpec, find_spec_dir

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB


class RangeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            start = int(range_header.split("=", 1)[1].split("-", 1)[0])
            body = PAYLOAD[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        else:
            body = PAYLOAD
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_spec() -> ModelSpec:
    return ModelSpec(
        key="test",
        name="test-model",
        repo="owner/repo",
        files=("model.bin",),
        required=("model.bin",),
        label="test",
        short_label="test",
        approx_mb=1,
        min_sizes={"model.bin": 10},
    )


def test_download_and_resume(tmp_path: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/model.bin"
        downloader = ModelDownloader()
        downloader._total = len(PAYLOAD)  # noqa: SLF001 - focused unit test
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            target = tmp_path / "model.bin"
            downloader._download_file(client, url, target, len(PAYLOAD), "test", "model.bin")  # noqa: SLF001
            assert target.read_bytes() == PAYLOAD

            # Interrupted download: existing .part is resumed with a Range request.
            resumed = tmp_path / "resumed.bin"
            part = resumed.with_name(resumed.name + ".part")
            part.write_bytes(PAYLOAD[:100_000])
            downloader._download_file(client, url, resumed, len(PAYLOAD), "test", "resumed.bin")  # noqa: SLF001
            assert resumed.read_bytes() == PAYLOAD
            assert not part.exists()
    finally:
        server.shutdown()
        server.server_close()


def test_find_spec_dir_uses_required_sizes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(models_module.paths, "model_roots", lambda: [tmp_path])
    spec = make_spec()
    model_dir = tmp_path / spec.name
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"x" * 20)
    assert find_spec_dir(spec) == model_dir
    (model_dir / "model.bin").write_bytes(b"short")
    assert find_spec_dir(spec) is None


def test_url_composition():
    spec = make_spec()
    assert (
        ModelDownloader._url("https://hf-mirror.com", spec, "model.bin")
        == "https://hf-mirror.com/owner/repo/resolve/main/model.bin"
    )
