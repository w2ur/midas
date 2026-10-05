#!/usr/bin/env python3
"""Derive the recorded marks of legacy passive-benchmark rows from git history.

    .venv/bin/python scripts/derive_legacy_benchmark_marks.py [--ref HEAD] [--dry-run]

A passive benchmark row written since plan 1.5 (2026-10-05) records the two
closes it was priced from (``mark_date``/``mark_close``, ``base_date``/
``base_close``; see ``engine.baselines.compute_passive_benchmark``), which is
what lets ``merge_baseline_series`` tell a point that forward-filled a close
that landed later from one whose close was revised. Rows published before
that carry no such fields, and **they are never rewritten to add them**: the
append-only gate freezes a published row byte for byte, and adding a field is
a change. Their marks go in a sidecar beside the series instead:

- ``data/baselines/<agent>/benchmark_marks.json``
- ``data/baselines/global/msci_world_marks.json``

each a JSON array of ``{date, mark_date, mark_close, base_date, base_close}``
sorted by date. The sidecar is itself a dated-row array under
``data/baselines/*/*.json``, so the append-only gate freezes it too.

## How a mark is derived

For each published legacy row, the commit that wrote the row's current bytes
is found by walking the series file's history. The store as it stood in that
commit (``data/market/ohlcv/<ticker>.jsonl``) is read, and the marks are what
``compute_passive_benchmark`` would have used there: the newest close on or
before the row's date and the first close on or after day one. **A mark is
accepted only if it reproduces the published ``portfolio_value`` exactly**
(the same ``initial * (mark / base)`` expression, compared with ``==``): the
closes are copied verbatim from the JSON, so anything but exact equality
means the derivation is not the writer's. A row that does not reproduce is
left out of the sidecar and named; the merge then counts its mismatches
``unclassified``. Measured on 2026-10-05 over the live history: 1,720 of
1,720 legacy rows across the ten priced series reproduce from the writer's
own tree with the roster's current ticker, so no fallback (the parent tree, a
ticker the roster named earlier) is implemented — one would be a path no row
exercises.

``EUR_CASH_FLAT`` benchmarks read no price, so there is nothing to record and
no sidecar is written for them.

## Exit codes (portfolio convention)

- 0: every legacy row of every priced series has marks.
- 1: a finding — some rows could not be derived (named on stdout); the rest
  are written.
- 2: unknown — not a git checkout, the ref does not resolve, a series cannot
  be read, or a series with legacy rows yielded no marks at all. Nothing is
  written in that case.

Read-only over git history; the only files it writes are the sidecars. An
existing sidecar entry is never changed: a re-derivation that disagrees with
one is reported as a finding and the entry is kept.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.baselines import MARK_FIELDS, marks_sidecar_path  # noqa: E402
from engine.config import get_config  # noqa: E402

EXIT_OK, EXIT_FINDING, EXIT_UNKNOWN = 0, 1, 2


class Unknown(Exception):
    """A condition under which no answer can be given."""


#: The checkout whose history is read and whose sidecars are written. A
#: module attribute so the tests can point it at a throwaway repository.
REPO_ROOT = _PROJECT_ROOT


def _git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise Unknown(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _show(ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def _closes(text: str | None) -> dict[str, float]:
    """The raw ``close`` per date, exactly as ``engine.baselines._load_ohlcv``."""
    out: dict[str, float] = {}
    if not text:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            out[row["date"]] = float(row["close"])
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _rows(text: str | None) -> dict[str, dict] | None:
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return {r["date"]: r for r in data if isinstance(r, dict) and "date" in r}


def series_to_derive(cfg) -> list[tuple[str, str]]:
    """``(repo-relative series path, ticker)`` for every price-driven benchmark."""
    out = []
    for agent_id in cfg.trading_roster:
        spec = cfg.roster[agent_id].benchmark
        if spec is None or spec.ticker == "EUR_CASH_FLAT":
            continue
        out.append((f"data/baselines/{agent_id}/benchmark.json", spec.ticker))
    if cfg.global_reference.ticker != "EUR_CASH_FLAT":
        out.append(("data/baselines/global/msci_world.json", cfg.global_reference.ticker))
    return out


def writers(ref: str, path: str) -> dict[str, str]:
    """``{date: commit}`` — the commit that introduced each row's bytes at ``ref``."""
    current: dict[str, str] = {}
    writer: dict[str, str] = {}
    for commit in _git("log", "--reverse", "--format=%H", ref, "--", path).split():
        rows = _rows(_show(commit, path))
        if rows is None:
            continue
        snapshot = {d: json.dumps(r, sort_keys=True) for d, r in rows.items()}
        for d, blob in snapshot.items():
            if current.get(d) != blob:
                writer[d] = commit
        current = snapshot
    return writer


def marks_for(
    closes: dict[str, float], row_date: str, day_one: str
) -> dict | None:
    """What ``compute_passive_benchmark`` priced ``row_date`` from, in ``closes``."""
    window = [d for d in closes if day_one <= d <= row_date]
    if not window:
        return None
    base, mark = min(window), max(window)
    return {
        "mark_date": mark,
        "mark_close": closes[mark],
        "base_date": base,
        "base_close": closes[base],
    }


def derive_series(
    ref: str, path: str, ticker: str, initial: float, day_one: str
) -> tuple[list[dict], list[str], int]:
    """Derived sidecar rows, underived dates, and the number of legacy rows."""
    published = _rows(_show(ref, path))
    if published is None:
        raise Unknown(f"{path} is unreadable at {ref}")
    legacy = {d: r for d, r in published.items() if "mark_date" not in r}
    if not legacy:
        return [], [], 0
    writer = writers(ref, path)
    store_cache: dict[tuple[str, str], dict[str, float]] = {}

    def store(commit: str, tk: str) -> dict[str, float]:
        key = (commit, tk)
        if key not in store_cache:
            store_cache[key] = _closes(_show(commit, f"data/market/ohlcv/{tk}.jsonl"))
        return store_cache[key]


    derived, missing = [], []
    for d in sorted(legacy):
        commit = writer.get(d)
        marks = None if commit is None else marks_for(store(commit, ticker), d, day_one)
        if marks is not None and (
            initial * (marks["mark_close"] / marks["base_close"])
            == legacy[d]["portfolio_value"]
        ):
            derived.append({"date": d, **marks})
        else:
            missing.append(d)
    return derived, missing, len(legacy)


def merge_sidecar(
    sidecar: Path, derived: list[dict]
) -> tuple[list[dict], list[str]]:
    """Existing entries kept as-is; new ones added. Returns (rows, disagreements)."""
    existing: dict[str, dict] = {}
    if sidecar.exists():
        try:
            existing = {r["date"]: r for r in json.loads(sidecar.read_text())}
        except (ValueError, TypeError, KeyError) as exc:
            raise Unknown(f"{sidecar} is unreadable: {exc}") from exc
    disagreements = []
    for row in derived:
        old = existing.get(row["date"])
        if old is None:
            existing[row["date"]] = row
        elif {k: old.get(k) for k in MARK_FIELDS} != {k: row[k] for k in MARK_FIELDS}:
            disagreements.append(row["date"])
    return [existing[d] for d in sorted(existing)], disagreements


def run(ref: str, dry_run: bool) -> int:
    _git("rev-parse", "--verify", f"{ref}^{{commit}}")
    cfg = get_config()
    initial = cfg.initial_capital
    day_one = cfg.day_one.isoformat()
    plan = []
    findings = 0
    for path, ticker in series_to_derive(cfg):
        derived, missing, n_legacy = derive_series(ref, path, ticker, initial, day_one)
        if n_legacy and not derived:
            raise Unknown(f"{path}: {n_legacy} legacy row(s), none derivable")
        print(
            f"{path} ({ticker}): {len(derived)}/{n_legacy} legacy row(s) derived"
        )
        if missing:
            findings += len(missing)
            print(f"  underived: {', '.join(missing)}")
        plan.append((path, derived))
    for path, derived in plan:
        if not derived:
            continue
        sidecar = marks_sidecar_path(REPO_ROOT / path)
        rows, disagreements = merge_sidecar(sidecar, derived)
        if disagreements:
            findings += len(disagreements)
            print(
                f"  {sidecar.name}: existing entries kept where the re-derivation "
                f"disagrees: {', '.join(disagreements)}"
            )
        if not dry_run:
            sidecar.write_text(json.dumps(rows, indent=2) + "\n")
    return EXIT_FINDING if findings else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default="HEAD", help="history to derive from")
    parser.add_argument(
        "--dry-run", action="store_true", help="derive and report, write nothing"
    )
    args = parser.parse_args(argv)
    try:
        return run(args.ref, args.dry_run)
    except Unknown as exc:
        print(f"UNKNOWN: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN


if __name__ == "__main__":
    sys.exit(main())
