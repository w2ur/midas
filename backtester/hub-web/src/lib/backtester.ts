// Pure request/response types + payload builder for the backtester tool island.
// Mirrors backtester/schemas.py (SignalRunRequest / RunResponse).

export interface Rules {
  max_positions: number;
  max_position_pct: number;
  min_hold_days: number;
}

export interface Preset {
  id: string;
  label: string;
  selector: string;
  manager: string;
  rules: Rules;
  default_universe: string;
}

export interface UniverseOption {
  id: string;
  label: string;
}

export interface Catalog {
  presets: Preset[];
  universes: UniverseOption[];
  date_bounds: { min: string; max: string };
  currencies: Array<'EUR' | 'USD'>;
}

export interface SignalRunRequest {
  kind: 'signal';
  config: { universe: string; selector: string; manager: string } & Rules;
  start_date: string;
  end_date: string;
  capital: number;
  currency: 'EUR' | 'USD';
}

export interface EquityPoint {
  date: string;
  value: number;
}

export interface RunResponse {
  equity_curve: EquityPoint[];
  benchmark_curve: EquityPoint[];
  benchmark_label: string;
  metrics: {
    total_return_pct: number;
    cagr_pct: number;
    sharpe: number;
    max_drawdown_pct: number;
    vs_msci_world_pct: number;
    vs_coin_flip_pct: number;
  };
  trades: Array<{
    date: string;
    ticker: string;
    side: 'buy' | 'sell';
    quantity: number;
    price: number;
    pnl: number | null;
  }>;
  config_hash: string;
  warnings: string[];
}

export function buildRunRequest(
  preset: Preset,
  universe: string,
  startDate: string,
  endDate: string,
  capital: number,
  currency: 'EUR' | 'USD',
): SignalRunRequest {
  return {
    kind: 'signal',
    config: {
      universe,
      selector: preset.selector,
      manager: preset.manager,
      max_positions: preset.rules.max_positions,
      max_position_pct: preset.rules.max_position_pct,
      min_hold_days: preset.rules.min_hold_days,
    },
    start_date: startDate,
    end_date: endDate,
    capital,
    currency,
  };
}

/**
 * Do two catalogs disagree about what the dropdowns should contain?
 *
 * Compares exactly what `fillOptions` renders — preset and universe ids and
 * labels — and nothing else. It deliberately ignores `date_bounds`, because
 * upstream's `max` is always "today" while a build-time snapshot carries its
 * build date: comparing whole payloads reports a difference on any day the site
 * was not rebuilt, and a needless rebuild resets a selection the visitor made
 * while the revalidation request was still in flight. Preset `rules` are
 * ignored for the same reason — they change the request, not the dropdown, and
 * the caller adopts the live catalog either way.
 */
export function optionsDiffer(a: Catalog, b: Catalog): boolean {
  const options = (c: Catalog) =>
    JSON.stringify([
      c.presets.map((p) => [p.id, p.label, p.default_universe]),
      c.universes.map((u) => [u.id, u.label]),
    ]);
  return options(a) !== options(b);
}

/**
 * How many retries fit a wake-up budget at a fixed interval.
 *
 * The backtester's Cloud Run backend scales to zero; its cold start (130-230s,
 * measured from Cloud Run's own logs) outlives Netlify's ~30s function
 * timeout, so `/api/backtest/catalog` comes back a 502 long before the
 * container has answered. Critically, the request Netlify gave up on still
 * reached Cloud Run and still woke it — a retry a little later succeeds. This
 * function only says *how many* retries fit in the budget; the caller owns
 * the actual waiting and re-fetching (see `wakeCatalog` in
 * `backtester-island.ts`). A fixed interval, not exponential backoff, because
 * the cold start is a roughly-known constant, not an unknown failure to back
 * off from.
 */
export function computeWakeupRetries(budgetMs: number, intervalMs: number): number {
  if (intervalMs <= 0) return 0;
  return Math.max(0, Math.floor(budgetMs / intervalMs));
}
