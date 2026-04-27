import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make `backtester` and `engine` importable when pytest runs from repo root.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from backtester.app import app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)
