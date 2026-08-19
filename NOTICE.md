# Notice — what the MIT licence covers

The MIT licence in `LICENSE` covers the **source code** in this repository:
`engine/`, `scripts/`, `tests/`, `app/`, `backtester/`, `site/src/`, `workers/`,
and the configuration that drives them.

It does **not** cover the following, which are included for reproducibility and
are not licensed for redistribution:

## Market data — `data/market/`

Daily OHLCV bars retrieved from Yahoo Finance via the `yfinance` library, cached
here so that every fill and valuation in this ledger can be re-derived from the
exact price series the broker saw (`git checkout <executed_sha>`). This data is
the property of its vendor and is redistributed here neither under the MIT
licence nor under any other grant. Anyone standing up their own desk should
populate their own store with `python scripts/fetch_ohlcv.py` rather than copy
this one. See `data/market/NOTICE.md`.

`data/tickers.json` is a vendor-supplied name registry populated by the same
fetch. The same reservation applies to it, including in `midas-core`, where it
is seeded as a bootstrap.

## Narrative content

The prose written by or about this desk — `METHODOLOGY.md`, `TAX.md`,
`data/blog/`, `data/posts/`, `data/agent_memory/`, and the editorial copy under
`site/src/pages/` — is published as a record of the experiment, not as a work
offered for reuse. All rights reserved.

Nothing here restricts reading, quoting, auditing or verifying any of it. The
whole point of publishing the ledger is that it can be checked.
