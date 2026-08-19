# Notice — what the MIT licence covers

The MIT licence in `LICENSE` covers the **source code** in this repository:
`engine/`, `scripts/`, `tests/`, `app/`, `backtester/`, `workers/`, `examples/`,
`docs/`, and the configuration that drives them. Within `site/src/`, the
licence covers the site's code — `components/`, `lib/`, `layouts/`, build
logic — but **not** `site/src/pages/`, whose editorial copy is reserved; see
"Narrative content" below.

It does **not** cover the following, which are included for reproducibility and
are not licensed for redistribution:

## Market data — `data/market/`

Daily OHLCV bars retrieved from Yahoo Finance via the `yfinance` library, cached
here so that a fill or valuation in this ledger can be re-derived from the
exact price series the broker saw (`git checkout <executed_sha>` — fills since
2026-06-29 only; see the Provenance section on `/open-source`). This data is
the property of its vendor and is redistributed here neither under the MIT
licence nor under any other grant. Anyone standing up their own desk should
populate their own store with `python scripts/fetch_ohlcv.py` rather than copy
this one. See `data/market/NOTICE.md`.

`data/market/news/` holds verbatim third-party headlines collected for the
sentiment-arm feed, attributed to their own publishers (Yahoo Finance, Motley
Fool, Trefis, Simply Wall St and others). This is a different class of content
with different rights holders from the price bars above, and is reserved on
the same terms: not licensed for redistribution. See `data/market/NOTICE.md`.

`data/tickers.json` is a vendor-supplied name registry populated by the same
fetch. The same reservation applies to it in this repository. It is also
seeded as a bootstrap file in `midas-core`, the framework mirror — but that
repository's own licensing of it is that repository's business, not asserted
here; this file has no mechanism to reach across repositories.

## Narrative content

The prose written by or about this desk — `METHODOLOGY.md`, `TAX.md`,
`data/blog/`, `data/posts/`, `data/agent_memory/`, and the editorial copy under
`site/src/pages/` (the page templates' prose and markup, not the code they
share with the rest of `site/src/`) — is published as a record of the
experiment, not as a work offered for reuse. All rights reserved.

Nothing here restricts reading, quoting, auditing or verifying any of it. The
whole point of publishing the ledger is that it can be checked.
