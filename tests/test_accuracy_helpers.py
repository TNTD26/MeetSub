from __future__ import annotations

import numpy as np

from meetsub.pipeline import glossary_to_hotwords
from meetsub.transcriber import SileroGate


def test_glossary_to_hotwords_extracts_english_side():
    glossary = "\n".join(
        [
            "SLA = 服务等级协议",
            "Tom Chen：陈汤姆",
            "- Kubernetes -> K8s",
            "不是术语的一行",
            "SLA = 重复项",
        ]
    )
    hotwords = glossary_to_hotwords(glossary)
    assert "SLA" in hotwords
    assert "Tom Chen" in hotwords
    assert "Kubernetes" in hotwords
    assert hotwords.count("SLA") == 1
    assert "服务等级" not in hotwords


def test_silero_gate_rejects_silence():
    gate = SileroGate()
    gate.load()
    if gate._model is None:  # noqa: SLF001 - optional native dependency
        return
    seconds = gate.speech_seconds(np.zeros(16000, dtype=np.float32))
    assert seconds is not None
    assert seconds < 0.1
