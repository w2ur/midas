"""Pins the Cloudflare gate's JS copies of Python rules to their originals.

`workers/trigger-gate/src/gate.js` re-implements three things that already
exist in Python: the crypto ticker allowlist, the expiry comparison, and the
list of pending channels. That duplication is deliberate — the Worker runs off
GitHub and cannot import `engine.triggers` — but this repo's most expensive
defect (the quote currency one) was exactly a hand-copied rule drifting from
its original, so the copy does not get to exist unpinned.

If this module is red, change `gate.js` and `engine/triggers.py` in the SAME
commit.

Live-only (see LIVE_ONLY_TESTS in scripts/sync_core.py): `workers/` is
live-desk infrastructure and is not part of the midas-core mirror.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from engine import triggers
from engine.config import get_config

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = REPO_ROOT / "workers" / "trigger-gate"
GATE = WORKER / "src" / "gate.js"
INDEX = WORKER / "src" / "index.js"


def _js_set(name: str) -> set[str]:
    """Read a `export const NAME = new Set([...])` literal out of gate.js."""
    match = re.search(
        rf"{name}\s*=\s*new Set\(\[(.*?)\]\)", GATE.read_text(encoding="utf-8"), re.S
    )
    assert match, f"{name} literal not found in gate.js — has the shape changed?"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


class TestCryptoClassificationParity:
    def test_bases_match_the_engine(self):
        assert _js_set("CRYPTO_BASES") == set(triggers._CRYPTO_BASES)

    def test_quotes_match_the_engine(self):
        assert _js_set("CRYPTO_QUOTES") == set(triggers._CRYPTO_QUOTES)

    def test_the_reader_can_actually_see_the_literals(self):
        """The control: a regex that silently matched nothing would make both
        assertions above vacuous on any file at all."""
        assert len(_js_set("CRYPTO_BASES")) > 5
        assert "BTC" in _js_set("CRYPTO_BASES")
        assert "EUR" in _js_set("CRYPTO_QUOTES")


class TestPendingChannelParity:
    """Every configured pending channel must be one the Worker reads.

    A channel absent from the Worker is a SILENT under-dispatch: orders in it
    are simply never gated, which looks identical to a quiet market. It would
    surface only as fills arriving a day late via the daily sweep.
    """

    def _worker_paths(self) -> list[str]:
        match = re.search(
            r"PENDING_PATHS\s*=\s*\[(.*?)\]", INDEX.read_text(encoding="utf-8"), re.S
        )
        assert match, "PENDING_PATHS literal not found in index.js"
        return re.findall(r'"([^"]+)"', match.group(1))

    def test_the_public_channel_is_read(self):
        assert "data/orders/pending" in self._worker_paths()

    def test_every_allocator_channel_is_read(self):
        cfg = get_config()
        expected = {
            f"data/orders/{cfg.allocator_spec(aid).channels_prefix}-pending"
            for aid in cfg.allocators
        }
        missing = expected - set(self._worker_paths())
        assert missing == set(), (
            f"roster.yaml declares allocator channels the Worker never reads: "
            f"{sorted(missing)}. Orders there would be gated by nobody."
        )

    def test_the_worker_reads_no_channel_that_does_not_exist(self):
        """The other direction: a stale path returns null and reads as empty."""
        for path in self._worker_paths():
            assert (REPO_ROOT / path).is_dir(), (
                f"the Worker reads {path}, which is not a directory in this repo; "
                "GraphQL answers null for it and the gate sees an empty channel"
            )


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestExpiryParity:
    """`isLive` must be the exact negation of `engine.triggers.is_expired`.

    Executed rather than pattern-matched: the question is what the two
    implementations ANSWER, and only one of them is Python.
    """

    CASES = [
        ("2026-09-30", "2026-09-29"),  # before expiry
        ("2026-09-30", "2026-09-30"),  # ON expiry — inclusive, so expired
        ("2026-09-30", "2026-10-01"),  # after
        (None, "2026-09-30"),  # no expiry never expires
    ]

    def _js_is_live(self, expires, today: str) -> bool:
        script = (
            f"import {{isLive}} from {json.dumps(str(GATE))};"
            f"console.log(isLive({json.dumps({'expires': expires})}, "
            f"{json.dumps(today)}) ? '1' : '0');"
        )
        out = subprocess.run(
            [NODE, "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() == "1"

    @pytest.mark.parametrize("expires,today", CASES)
    def test_is_live_is_the_negation_of_is_expired(self, expires, today):
        from datetime import date

        from engine.orders import Order

        order = Order(
            order_id="ord_test",
            ts=None,
            agent_id="a",
            action="BUY",
            ticker="BTC-EUR",
            shares=1.0,
            reasoning="r",
            currency="EUR",
            trigger={"op": ">=", "level": 1.0},
            expires=expires,
        )
        python_live = not triggers.is_expired(order, date.fromisoformat(today))
        assert self._js_is_live(expires, today) == python_live, (
            f"gate.js isLive disagrees with engine.triggers.is_expired for "
            f"expires={expires!r} on {today}"
        )

    def test_the_bridge_can_disagree(self):
        """The control: this comparison is only evidence if a wrong JS answer
        would actually be visible through it."""
        assert self._js_is_live("2026-09-30", "2026-09-29") is True
        assert self._js_is_live("2026-09-30", "2026-09-30") is False
