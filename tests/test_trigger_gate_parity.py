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


WRANGLER = WORKER / "wrangler.toml"
FETCH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "fetch-ohlcv.yml"


def _wrangler_crons() -> list[str]:
    match = re.search(r"^crons\s*=\s*\[(.*?)\]", WRANGLER.read_text(encoding="utf-8"), re.M | re.S)
    assert match, "no `crons = [...]` in wrangler.toml"
    return re.findall(r'"([^"]+)"', match.group(1))


def _close_runs() -> dict[str, str]:
    """The `CLOSE_RUNS = {...}` literal in index.js: cron -> bucket."""
    match = re.search(
        r"export const CLOSE_RUNS\s*=\s*\{(.*?)\};", INDEX.read_text(encoding="utf-8"), re.S
    )
    assert match, "CLOSE_RUNS literal not found in index.js"
    return dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', match.group(1)))


def _cron_hours(cron: str) -> set[int]:
    """The hours a 5-field cron's hour field names (lists and ranges only)."""
    hours: set[int] = set()
    for part in cron.split()[1].split(","):
        lo, _, hi = part.partition("-")
        hours.update(range(int(lo), int(hi or lo) + 1))
    return hours


def _cron_time(cron: str) -> tuple[int, int]:
    minute, hour = cron.split()[:2]
    return int(hour), int(minute)


class TestCloseRunCronParity:
    """The Worker's close-run crons (2026-09-28) are pinned to the session they
    feed and to the vendor window they were measured against.

    Three copies of one fact would otherwise drift apart: wrangler.toml
    schedules the crons, index.js maps them to a `close_run` bucket, and
    scripts/check_triggers.py's SESSION_START is the deadline they exist to
    meet. A session move that forgets the Worker is a fetch that lands after
    the session — the morning-only regime back, with nothing red.
    """

    GATE_CRON = "0 0-21,23 * * *"

    def test_every_close_run_cron_is_scheduled(self):
        assert set(_close_runs()) <= set(_wrangler_crons())

    def test_every_scheduled_cron_is_the_gate_or_a_close_run(self):
        """The other direction: a cron added to wrangler.toml and mapped to
        nothing would run the gate at an hour nobody chose for it."""
        assert set(_wrangler_crons()) - set(_close_runs()) == {self.GATE_CRON}

    def test_the_two_buckets_are_the_scripts(self):
        assert sorted(_close_runs().values()) == ["eu", "us"]

    def test_the_gate_skips_the_session_hour_and_only_that_hour(self):
        from scripts import check_triggers as ct

        hours = _cron_hours(self.GATE_CRON)
        assert ct.SESSION_START.hour not in hours, (
            "the gate cron runs in the session hour, inside the watcher blackout"
        )
        assert hours | {ct.SESSION_START.hour} == set(range(24))

    def test_the_european_run_sits_inside_the_measured_window(self):
        """eu-close-probe.yml (2026-08-14..17): populated from ~17:00 UTC, still
        there at 20:18, a null row by 22:23. Every EU_CLOSE_SUFFIXES venue has
        closed by 16:30 UTC in winter."""
        eu = next(c for c, b in _close_runs().items() if b == "eu")
        assert (17, 0) <= _cron_time(eu) <= (20, 0), eu

    def test_the_us_run_is_after_the_winter_close_and_before_the_session(self):
        """The US cash close is 21:00 UTC in winter; the run needs ~10 min and
        the session reads the store at SESSION_START."""
        from datetime import datetime, timedelta

        from scripts import check_triggers as ct

        us = next(c for c, b in _close_runs().items() if b == "us")
        hh, mm = _cron_time(us)
        start = datetime(2026, 1, 5, hh, mm)
        assert start >= datetime(2026, 1, 5, 21, 5), us
        assert start + timedelta(minutes=25) <= datetime.combine(
            start.date(), ct.SESSION_START
        ), f"{us} leaves the session less than 25 min after the US close run starts"

    def test_the_close_runs_are_weekday_only(self):
        for cron in _close_runs():
            assert cron.split()[4] == "1-5", cron

    def test_the_input_the_worker_dispatches_exists(self):
        import yaml

        spec = yaml.safe_load(FETCH_WORKFLOW.read_text(encoding="utf-8"))
        on = spec[True] if True in spec else spec["on"]
        options = on["workflow_dispatch"]["inputs"]["close_run"]["options"]
        assert set(_close_runs().values()) <= set(options), options
