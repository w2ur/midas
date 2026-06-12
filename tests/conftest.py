"""Shared pytest fixtures for the Midas test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_session_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the session-state directory to a tmp path for every test.

    The ``idempotent_step`` decorator in ``scripts.daily_session`` calls
    ``scripts.session_state.is_done`` / ``mark_done``, which resolves
    ``_STATE_DIR`` at call time from the ``session_state`` module namespace.
    Monkeypatching ``_STATE_DIR`` here prevents step-completion markers from
    leaking between tests and eliminates side-effects from the real
    ``data/session_state/`` directory.
    """
    import scripts.session_state as ss

    monkeypatch.setattr(ss, "_STATE_DIR", tmp_path / "session_state")
