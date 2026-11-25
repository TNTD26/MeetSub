"""Download the optional local models used by MeetSub.

Examples:
    python scripts/download_models.py --all
    python scripts/download_models.py --model small.en --model m2m100-418m
    python scripts/download_models.py --endpoint https://huggingface.co --all
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meetsub.models import ASR_SPECS, TRANSLATION_SPECS, ModelSpec  # noqa: E402
from meetsub.paths import writable_model_root  # noqa: E402
from meetsub.utils import human_bytes  # noqa: E402

CORE = ("small.en", "qwen2.5-1.5b", "m2m100-418m", "opus-mt")
CHOICES = {**ASR_SPECS, **TRANSLATION_SPECS}
NAME_TO_SPEC = {spec.name: spec for spec in CHOICES.values()}


def files_ready(spec: ModelSpec, target: Path) -> bool:
    for relative in spec.required:
        path = target / relative
        if not path.is_file():
            return False
        minimum = spec.min_sizes.get(relative)
        if minimum is not None and path.stat().st_size < minimum:
            return False
    return True


def remote_size(client: httpx.Client, url: str) -> int:
    try:
        response = client.head(url)
        if response.status_code < 400:
            raw = response.headers.get("content-length", "")
            return int(raw) if raw.isdigit() else 0
    except Exception:
        pass
    return 0


def download_file(
    client: httpx.Client,
    url: str,
    destination: Path,
    expected: int,
    label: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    if destination.exists() and expected > 0 and destination.stat().st_size == expected:
        print(f"  = {label} already present")
        return
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            start = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={start}-"} if start else {}
            mode = "ab" if start else "wb"
            with client.stream(
                "GET", url, headers=headers, timeout=httpx.Timeout(60.0, connect=20.0)
            ) as response:
                if response.status_code == 416 and start:
                    os.replace(part, destination)
                    print(f"  = {label} resumed")
                    return
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}")
                if start and response.status_code != 206:
                    start = 0
                    mode = "wb"
                total = expected or int(response.headers.get("content-length", "0") or 0)
                done = start
                last_print = 0.0
                with part.open(mode) as handle:
                    for chunk in response.iter_bytes(1024 * 256):
                        handle.write(chunk)
                        done += len(chunk)
                        if expected and done > expected:
                            done = expected
                        now = time.monotonic()
                        if now - last_print > 0.4 or done == total:
                            last_print = now
                            pct = f"{done / total * 100:5.1f}%" if total else human_bytes(done)
                            print(f"\r  ↓ {label} {pct}  {human_bytes(done)}", end="", flush=True)
                print()
                actual = part.stat().st_size
                if expected > 0 and actual != expected:
                    raise OSError(f"incomplete ({actual}/{expected} bytes)")
                os.replace(part, destination)
                return
        except Exception as exc:
            last_error = exc
            print(f"\n  ! {label} attempt {attempt} failed: {exc}")
            time.sleep(1.2 * attempt)
    raise RuntimeError(f"could not download {label}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download MeetSub offline models")
    parser.add_argument(
        "--model",
        action="append",
        default=None,
        help=f"model key or name: {', '.join(sorted(CHOICES))}",
    )
    parser.add_argument("--all", action="store_true", help="download the three default models")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("MEETSUB_HF_ENDPOINT", "https://hf-mirror.com"),
        help="model host (default: hf-mirror.com)",
    )
    parser.add_argument("--dir", type=Path, default=None, help="target models directory")
    args = parser.parse_args()

    if args.all:
        requested = list(CORE)
    elif args.model:
        requested = args.model
    else:
        requested = ["small.en", "qwen2.5-1.5b"]
    specs: list[ModelSpec] = []
    for name in requested:
        spec = CHOICES.get(name) or NAME_TO_SPEC.get(name)
        if spec is None:
            print(f"Unknown model: {name}")
            return 2
        if spec not in specs:
            specs.append(spec)

    target_root = args.dir or writable_model_root()
    endpoint = args.endpoint.rstrip("/")
    print(f"Target: {target_root}")
    print(f"Source: {endpoint}")
    print(f"Models: {', '.join(spec.name for spec in specs)}")

    with httpx.Client(
        follow_redirects=True,
        headers={"User-Agent": "MeetSub/1.0 model downloader"},
    ) as client:
        for spec in specs:
            target = target_root / spec.name
            if files_ready(spec, target):
                print(f"[ready] {spec.label}")
                continue
            print(f"[download] {spec.label} -> {target}")
            for filename in spec.files:
                destination = target / filename
                url = f"{endpoint}/{spec.repo}/resolve/main/{filename}"
                expected = remote_size(client, url)
                download_file(client, url, destination, expected, filename)
            if not files_ready(spec, target):
                print(f"[error] {spec.label} is still incomplete")
                return 1
            print(f"[done] {spec.label}")

    print("All requested models are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
