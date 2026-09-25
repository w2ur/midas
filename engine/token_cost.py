"""Lightweight token/cost visibility for persona dispatch.

We do not have real token accounting from the orchestrator's untracked LLM
dispatches, so this module provides a cheap, deterministic *proxy*: characters
divided by four, the widely-used rough tokens-per-char heuristic for English.
It is a visibility signal (how heavy was today's prompt load, and which agent
dominated it), not a billing figure.

The persona dispatch path (``engine.persona_dispatch.wrap_persona_prompt``) feeds
every wrapped prompt into the session ledger. The daily output bundle reads the
accumulated totals into a ``session_costs`` block.

The ledger is persisted, one JSON row per dispatch
-------------------------------------------------
The orchestrator runs each step as its own ``python -c`` process, so a ledger
held only in memory was empty in the process that assembled the bundle: the
2026-09-23 bundle recorded 0 dispatches against the 33 the session made, and
every bundle before it read the same way. Each dispatch is therefore appended
to ``<session_state_dir>/dispatch_ledger.jsonl`` (gitignored), and the totals
are rebuilt from that file. ``scripts.session_guard.anchor_session`` resets it
at Step 0c, so a session counts its own dispatches only.

A torn or unreadable line is skipped with a warning, never raised: losing a
session over a visibility counter would cost far more than a miscount.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from engine.config import get_config

_CHARS_PER_TOKEN = 4
PROXY_LABEL = "len/4"
LEDGER_FILENAME = "dispatch_ledger.jsonl"


def estimate_tokens(text: str | None) -> int:
    """Return the character-count token proxy: ``len(text) // 4``.

    Deterministic and network-free. ``None``/empty → 0.
    """
    if not text:
        return 0
    return len(text) // _CHARS_PER_TOKEN


class SessionCostLedger:
    """Accumulates per-dispatch prompt-size proxies across a session.

    Keyed by agent id. Each entry tracks the dispatch count, total prompt
    characters, and total estimated tokens (the ``len/4`` proxy). ``totals``
    returns a JSON-serializable block suitable for the output bundle.
    """

    def __init__(self) -> None:
        self._by_agent: dict[str, dict[str, int]] = {}

    def record(self, agent_id: str, prompt: str) -> int:
        """Record one dispatch for ``agent_id``. Returns the dispatch's token proxy."""
        return self._add(agent_id, len(prompt) if prompt else 0, estimate_tokens(prompt))

    def _add(self, agent_id: str, chars: int, est: int) -> int:
        entry = self._by_agent.setdefault(
            agent_id, {"dispatches": 0, "prompt_chars": 0, "est_tokens": 0}
        )
        entry["dispatches"] += 1
        entry["prompt_chars"] += chars
        entry["est_tokens"] += est
        return est

    def reset(self) -> None:
        self._by_agent.clear()

    @property
    def is_empty(self) -> bool:
        return not self._by_agent

    def totals(self) -> dict:
        """Return the session totals block.

        Shape::

            {"proxy": "len/4", "total_dispatches": int, "total_prompt_chars": int,
             "total_est_tokens": int, "by_agent": {id: {dispatches, prompt_chars,
             est_tokens}}}
        """
        by_agent = {aid: dict(entry) for aid, entry in self._by_agent.items()}
        return {
            "proxy": PROXY_LABEL,
            "total_dispatches": sum(e["dispatches"] for e in by_agent.values()),
            "total_prompt_chars": sum(e["prompt_chars"] for e in by_agent.values()),
            "total_est_tokens": sum(e["est_tokens"] for e in by_agent.values()),
            "by_agent": by_agent,
        }


def __getattr__(name: str) -> object:
    """Expose ``_LEDGER_PATH`` lazily (PEP 562), mirroring ``session_state``.

    ``None`` means "resolve from config"; the test suite sets a per-test path.
    """
    if name == "_LEDGER_PATH":
        return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _ledger_path() -> Path:
    override = globals().get("_LEDGER_PATH")
    if override is not None:
        return Path(override)
    return get_config().session_state_dir / LEDGER_FILENAME


def record_dispatch(agent_id: str, prompt: str) -> int:
    """Append one dispatch to the session ledger. Returns the token proxy."""
    chars = len(prompt) if prompt else 0
    est = estimate_tokens(prompt)
    row = {"agent_id": agent_id, "prompt_chars": chars, "est_tokens": est}
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # One short line per write, opened in append mode: the parallel dispatch
    # rounds are prepared from separate processes, and appends of this size
    # do not interleave.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return est


def _load_ledger() -> SessionCostLedger:
    ledger = SessionCostLedger()
    path = _ledger_path()
    if not path.exists():
        return ledger
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            ledger._add(
                str(row["agent_id"]), int(row["prompt_chars"]), int(row["est_tokens"])
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            print(
                f"[token_cost] skipping unreadable dispatch ledger line {lineno} "
                f"in {path}",
                file=sys.stderr,
            )
    return ledger


def session_cost_totals() -> dict:
    """Return the session totals block, rebuilt from the persisted ledger."""
    return _load_ledger().totals()


def reset_session_costs() -> None:
    """Clear the session ledger (called by ``anchor_session`` and in tests)."""
    _ledger_path().unlink(missing_ok=True)
