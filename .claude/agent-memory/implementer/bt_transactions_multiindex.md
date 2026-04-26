---
name: bt transactions MultiIndex shape
description: bt's get_transactions() returns a MultiIndex (Date, Security) DataFrame, not a flat DataFrame with Date/Security columns
type: project
---

When bt runs a backtest, `result.transactions` is a `pd.DataFrame` with a `pd.MultiIndex` of `(Date, Security)` — the index levels — and only `price` and `quantity` as columns. Iterating with `iterrows()` yields `(idx, row)` where `idx` is a tuple `(pd.Timestamp, str)`.

Unit test fixtures that simulate transactions often use a flat DataFrame with `Date` and `Security` as columns, which is a different shape. Code handling bt transactions must accommodate both shapes.

**Why:** Discovered when wiring the `/run` endpoint: `extract_top_trades` was written against the flat fixture shape, causing a `KeyError: 'Date'` on real bt output.

**How to apply:** In `backtester/trades.py`, detect `isinstance(transactions.index, pd.MultiIndex)` and unpack the index tuple accordingly. The fix is already in place.
