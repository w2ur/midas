# Market data — not covered by the repository's MIT licence

The `.jsonl` files under `ohlcv/` are daily bars retrieved from Yahoo Finance
through the `yfinance` library. They are committed so that:

- the sandboxed session can price a book with no outbound HTTP, and
- any fill can be re-derived from the exact series the broker saw, via the
  `executed_sha` stamped on it.

They are vendor data. They are **not** licensed for redistribution, and the MIT
licence in the repository root does not extend to them. See `NOTICE.md`.

To build your own store: `python scripts/fetch_ohlcv.py`.

Quarantined rows live in `quarantine/` and are subject to the same reservation.
