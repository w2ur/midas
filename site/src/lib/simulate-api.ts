import type { SimulateConfig } from "./simulate-config";

export type EquityPoint = { date: string; value: number };

export type MetricsBlock = {
  total_return_pct: number;
  cagr_pct: number;
  sharpe: number;
  max_drawdown_pct: number;
  vs_msci_world_pct: number;
  vs_coin_flip_pct: number;
};

export type TradeEntry = {
  date: string;
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  pnl: number | null;
};

export type RunResponse = {
  equity_curve: EquityPoint[];
  benchmark_curve: EquityPoint[];
  benchmark_label: string;
  metrics: MetricsBlock;
  trades: TradeEntry[];
  config_hash: string;
  warnings: string[];
};

const BACKTESTER_URL =
  import.meta.env.PUBLIC_BACKTESTER_URL ?? "http://localhost:8080";

export async function pingBacktester(): Promise<void> {
  try {
    await fetch(`${BACKTESTER_URL}/healthz`, { mode: "cors" });
  } catch {
    /* warming up; submit will retry */
  }
}

export async function runBacktest(
  config: SimulateConfig,
): Promise<RunResponse> {
  const response = await fetch(`${BACKTESTER_URL}/run`, {
    method: "POST",
    mode: "cors",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Backtest failed (${response.status}): ${text}`);
  }
  return (await response.json()) as RunResponse;
}
