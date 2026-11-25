from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Isolate any code under test that resolves per-user paths.
os.environ.setdefault("MEETSUB_DATA_DIR", str(ROOT / "_test_data"))


@pytest.fixture()
def vault(tmp_path):
    from meetsub.config import SecretsVault

    return SecretsVault(tmp_path / "secrets.bin")
