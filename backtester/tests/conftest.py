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


@pytest.fixture(autouse=True)
def _clear_backtester_secret(monkeypatch):
    # Isolate tests from a developer's exported BACKTESTER_SECRET — otherwise
    # test_run_* would 401 for anyone with the secret set in their shell.
    # Auth tests' own monkeypatch.setenv calls still override this afterward.
    monkeypatch.delenv("BACKTESTER_SECRET", raising=False)
