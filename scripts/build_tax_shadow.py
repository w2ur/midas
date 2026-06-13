"""Build data/tax_shadow/{agent}.json for all trading agents.

Reads each agent's ``data/portfolios/{agent}/trades.json``, computes the
French PFU shadow ledger via ``engine.tax_shadow.compute_tax_shadow``, and
writes the result to ``data/tax_shadow/{agent}.json``.

This script is REPORTING ONLY — it never mutates portfolio state.

Usage:
    python scripts/build_tax_shadow.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.tax_shadow import compute_tax_shadow

_PORTFOLIOS_DIR = _PROJECT_ROOT / "data" / "portfolios"
_OUTPUT_DIR = _PROJECT_ROOT / "data" / "tax_shadow"

# Directories inside data/portfolios/ that are NOT trading agents.
_NON_AGENT_DIRS = {"baseline-manager", "the-manager"}


def build_tax_shadow_all() -> list[str]:
    """Compute and write tax shadow ledgers for all trading agents.

    Returns
    -------
    list[str]
        Agent IDs for which a ledger was written.
    """
    if not _PORTFOLIOS_DIR.exists():
        print("  No portfolios directory found — skipping.")
        return []

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    written: list[str] = []

    for agent_dir in sorted(_PORTFOLIOS_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_id = agent_dir.name
        if agent_id in _NON_AGENT_DIRS:
            continue

        trades_path = agent_dir / "trades.json"
        if not trades_path.exists():
            print(f"  [SKIP] {agent_id}: no trades.json")
            continue

        try:
            trades = json.loads(trades_path.read_text())
        except Exception as exc:
            print(f"  [WARN] {agent_id}: could not read trades.json — {exc}")
            continue

        try:
            result = compute_tax_shadow(trades, agent=agent_id)
        except Exception as exc:
            print(f"  [WARN] {agent_id}: compute_tax_shadow failed — {exc}")
            continue

        out_path = _OUTPUT_DIR / f"{agent_id}.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

        sec_pfu = result["securities"]["lifetime_pfu"]
        crypto_pfu = result["crypto"]["lifetime_pfu"]
        print(
            f"  {agent_id}: sec_pfu={sec_pfu:.2f}  crypto_pfu={crypto_pfu:.2f}  → {out_path.name}"
        )
        written.append(agent_id)

    return written


def main() -> None:
    print(f"\n=== Build tax shadow ledgers ===")
    written = build_tax_shadow_all()
    print(f"\n  Done — wrote {len(written)} ledger(s).")


if __name__ == "__main__":
    main()
