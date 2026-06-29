"""Fetch and commit sanitized news digests for active portfolio tickers.

Runs in a trusted environment (local dev or GitHub Actions) where yfinance
works reliably. Output lives at data/market/news/{SYMBOL}.jsonl — one row per
run-day per ticker, committed to git so the sandboxed session can read it with
no network dependency (mirrors the OHLCV collector pattern).

Active tickers = union of:
  - tickers held across all data/portfolios/*/portfolio.json
  - tickers referenced by data/orders/pending/*.json

Empty news → no file (no empty file churn).
Same-day re-run → REPLACES that day's row (idempotent).

Usage:
    python scripts/fetch_sentiment.py
    python scripts/fetch_sentiment.py --dry-run     # list active tickers
    python scripts/fetch_sentiment.py --date 2026-06-13  # explicit date
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.config import get_config

_MAX_HEADLINES = 10
_MAX_TITLE_LEN = 200

# Pre-compiled sanitization patterns
_RE_URL = re.compile(r"https?://\S+|www\.\S+")
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_RE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_RE_WHITESPACE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


def sanitize_headline(raw: str | None) -> str:
    """Return a clean, safe headline string suitable for LLM context.

    Strips URLs, HTML tags, markdown link syntax, and control characters.
    Collapses whitespace (including newlines — the load-bearing anti-injection
    framing step). Hard-caps at 200 characters.

    This text will later reach analyst LLMs (S2) — it must be clean DATA,
    never a vector for injected instructions.

    Ordering matters:
    1. Markdown links first — preserves visible link text before URL removal.
    2. HTML tags (fixpoint loop) — must run BEFORE URL strip so that
       attributes like href="http://..." are removed with the tag and cannot
       leave unstrippable fragments behind.
    3. URLs — catches bare URLs that remain after tag removal.
    4. Control characters.
    5. Whitespace collapse (newlines included — anti-prompt-injection framing).
    6. 200-char cap.
    """
    if not raw:
        return ""
    text = str(raw)
    # 1. Strip markdown link syntax (preserves link text)
    text = _RE_MD_LINK.sub(r"\1", text)
    # 2. Strip HTML tags — fixpoint loop to handle nested/malformed tags
    #    e.g. "<scr<script>ipt>bad": first pass removes <script>, leaving
    #    "<scr" prefix and "ipt>" suffix as orphaned fragments. After the
    #    fixpoint loop converges, remaining lone '<' and '>' are stripped too.
    #    Capped at 10 iterations to avoid pathological loops on adversarial input.
    for _ in range(10):
        stripped = _RE_HTML_TAG.sub("", text)
        if stripped == text:
            break
        text = stripped
    # Remove orphaned angle brackets left by nested/malformed markup
    text = text.replace("<", "").replace(">", "")
    # 3. Strip bare URLs (after tags so href="..." attrs are already gone)
    text = _RE_URL.sub("", text)
    # 4. Strip control characters
    text = _RE_CONTROL.sub("", text)
    # 5. Collapse whitespace (newlines, tabs → single space — anti-injection framing)
    text = _RE_WHITESPACE.sub(" ", text).strip()
    # 6. Hard cap
    return text[:_MAX_TITLE_LEN]


# ---------------------------------------------------------------------------
# Active ticker scoping
# ---------------------------------------------------------------------------


def _collect_active_tickers() -> set[str]:
    """Return held tickers ∪ pending-order tickers (bounded surface area)."""
    tickers: set[str] = set()
    cfg = get_config()
    portfolios_dir = cfg.portfolios_dir
    pending_dir = cfg.orders_dir / "pending"

    # Held positions across all portfolios
    if portfolios_dir.exists():
        for portfolio_dir in portfolios_dir.iterdir():
            portfolio_file = portfolio_dir / "portfolio.json"
            if not portfolio_file.exists():
                continue
            try:
                with portfolio_file.open() as f:
                    data = json.load(f)
                for position in data.get("positions", []):
                    ticker = position.get("ticker")
                    if ticker:
                        tickers.add(ticker)
            except Exception as exc:
                print(f"  ! Could not read {portfolio_file}: {exc}", file=sys.stderr)

    # Pending conditional orders
    if pending_dir.exists():
        for order_file in pending_dir.glob("*.json"):
            try:
                with order_file.open() as f:
                    data = json.load(f)
                ticker = data.get("ticker")
                if ticker:
                    tickers.add(ticker)
            except Exception as exc:
                print(f"  ! Could not read {order_file}: {exc}", file=sys.stderr)

    return tickers


# ---------------------------------------------------------------------------
# yfinance adapter — all shape-handling lives here
# ---------------------------------------------------------------------------


def _fetch_news(symbol: str) -> list[dict]:
    """Fetch and normalize news from yfinance for a single symbol.

    Returns a list of normalized dicts with keys: title, source, published_at.
    Returns empty list on any error (yfinance is flaky — never crash the run).

    yfinance .news shape is unstable; all field access is defensive.
    Items are assumed newest-first as returned by yfinance.
    """
    import yfinance as yf

    try:
        raw_items = yf.Ticker(symbol).news or []
    except Exception as exc:
        print(f"  ! {symbol}: news fetch error — {exc}", file=sys.stderr)
        return []

    normalized: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        # yfinance may nest content inside a 'content' key (shape varies)
        content = item.get("content", item)
        if not isinstance(content, dict):
            content = item

        # Title: try multiple known field names
        raw_title = (
            content.get("title") or content.get("headline") or item.get("title") or ""
        )
        title = sanitize_headline(raw_title)
        if not title:
            continue

        # Source: best-effort
        provider = content.get("provider") or item.get("publisher") or {}
        if isinstance(provider, dict):
            source = provider.get("displayName") or provider.get("name") or "unknown"
        else:
            source = str(provider) if provider else "unknown"

        # Published timestamp: try multiple known field names
        published_at = (
            content.get("pubDate")
            or content.get("publishedAt")
            or item.get("providerPublishTime")
            or ""
        )
        # yfinance sometimes returns a Unix timestamp integer
        if isinstance(published_at, (int, float)):
            from datetime import datetime, timezone

            published_at = datetime.fromtimestamp(
                published_at, tz=timezone.utc
            ).isoformat()
        else:
            published_at = str(published_at) if published_at else ""

        normalized.append(
            {
                "title": title,
                "source": str(source),
                "published_at": published_at,
            }
        )

    return normalized


# ---------------------------------------------------------------------------
# JSONL I/O
# ---------------------------------------------------------------------------


def _existing_dates(path: Path) -> set[str]:
    """Read all date values from an existing JSONL file."""
    if not path.exists():
        return set()
    dates: set[str] = set()
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = row.get("date")
            if d:
                dates.add(d)
    return dates


def _write_digest(
    symbol: str,
    items: list[dict],
    run_date: str,
    news_dir: Path | None = None,
) -> None:
    """Write (or replace) the daily digest row for one ticker.

    Idempotency: if a row for (symbol, run_date) already exists, it is
    REPLACED by rewriting the file without that row and appending the new one.

    items must already be normalized (title/source/published_at).
    """
    if news_dir is None:
        news_dir = get_config().data_dir / "data" / "market" / "news"

    # Truncate to newest 10
    headlines = items[:_MAX_HEADLINES]

    record = {
        "date": run_date,
        "ticker": symbol,
        "headlines": [
            {
                "title": h["title"],
                "source": h["source"],
                "published_at": h["published_at"],
            }
            for h in headlines
        ],
        "count": len(headlines),
    }

    path = news_dir / f"{symbol}.jsonl"
    news_dir.mkdir(parents=True, exist_ok=True)

    # Filter out any existing row for this date (replace semantics)
    existing_rows: list[str] = []
    if path.exists():
        with path.open() as f:
            for line in f:
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                try:
                    row = json.loads(line_stripped)
                    if row.get("date") == run_date:
                        continue  # Drop — will be replaced by new record
                    existing_rows.append(line_stripped)
                except json.JSONDecodeError:
                    existing_rows.append(line_stripped)

    with path.open("w") as f:
        for line in existing_rows:
            f.write(line + "\n")
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Core run logic (separated from CLI so tests can call it directly)
# ---------------------------------------------------------------------------


def run(run_date: str) -> None:
    """Fetch and commit news digests for all active tickers."""
    active = _collect_active_tickers()
    symbols = sorted(active)

    print(f"Active tickers: {len(symbols)}")

    written = 0
    skipped_empty = 0
    failures = 0

    for symbol in symbols:
        try:
            items = _fetch_news(symbol)
        except Exception as exc:
            print(f"  ! {symbol}: unexpected error — {exc}", file=sys.stderr)
            failures += 1
            continue

        if not items:
            skipped_empty += 1
            continue

        _write_digest(symbol, items, run_date=run_date)
        written += 1
        print(f"  {symbol}: {len(items[:_MAX_HEADLINES])} headlines")

    print(
        f"\nDone. Written: {written}, skipped (no news): {skipped_empty}, "
        f"failures: {failures}."
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Run date in YYYY-MM-DD format (default: today UTC)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List active tickers without fetching",
    )
    args = parser.parse_args()

    run_date = args.date or date.today().isoformat()

    if args.dry_run:
        active = _collect_active_tickers()
        print(f"Active tickers ({len(active)}):")
        for t in sorted(active):
            print(f"  {t}")
        return 0

    run(run_date=run_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
