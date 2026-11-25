from __future__ import annotations

from meetsub.audio import AudioEndpoint, _Resampler, resolve_endpoint
from meetsub.config import AudioConfig


def endpoint(index: int, name: str, default: bool = False) -> AudioEndpoint:
    return AudioEndpoint(
        index=index,
        name=name,
        display_name=name.replace(" [Loopback]", ""),
        sample_rate=48000,
        channels=2,
        is_default=default,
    )


def test_resolve_endpoint_by_stable_name():
    cfg = AudioConfig(device_name="Headphones [Loopback]")
    chosen, note = resolve_endpoint(
        cfg, [endpoint(3, "Speakers [Loopback]", True), endpoint(2, "Headphones [Loopback]")]
    )
    assert chosen.index == 2
    assert note == ""


def test_resolve_endpoint_falls_back_to_default():
    cfg = AudioConfig(device_name="Missing device")
    chosen, note = resolve_endpoint(
        cfg, [endpoint(3, "Speakers [Loopback]", True), endpoint(2, "Headphones [Loopback]")]
    )
    assert chosen.index == 3
    assert "自动改用" in note


def test_empty_endpoint_list_is_an_error():
    import pytest

    from meetsub.audio import AudioCaptureError

    with pytest.raises(AudioCaptureError):
        resolve_endpoint(AudioConfig(), [])


def test_resampler_48k_to_16k():
    import numpy as np

    resampler = _Resampler(48000)
    out = resampler.process(np.zeros(4800, dtype=np.float32))
    assert out.size <= 1600
    assert out.dtype == np.float32
