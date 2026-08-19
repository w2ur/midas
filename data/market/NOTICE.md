# Market data — not covered by the repository's MIT licence

## Price bars — `ohlcv/`

The `.jsonl` files under `ohlcv/` are daily bars retrieved from Yahoo Finance
through the `yfinance` library. They are committed so that:

- the sandboxed session can price a book with no outbound HTTP, and
- a fill can be re-derived from the exact series the broker saw, via the
  `executed_sha` stamped on it — fills since 2026-06-29 only; earlier fills
  predate the stamping and carry no `executed_sha`.

They are vendor data. They are **not** licensed for redistribution, and the MIT
licence in the repository root does not extend to them. See `NOTICE.md`.

To build your own store: `python scripts/fetch_ohlcv.py`.

Quarantined rows live in `quarantine/` and are subject to the same reservation.

## Sentiment headlines — `news/`

The `.jsonl` files under `news/` are verbatim third-party headlines collected
for the sentiment-arm feed, attributed to their own publishers (Yahoo Finance,
Motley Fool, Trefis, Simply Wall St and others) rather than to this project.
They are a different class of content with different rights holders from the
price bars above, and are reserved on the same terms: committed for
reproducibility of the sentiment A/B experiment, **not** licensed for
redistribution.
