import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";

export type PortfolioSnapshot = {
  date: string;
  portfolio_value: number;
};

export function loadPortfolioSnapshots(agentId: string): PortfolioSnapshot[] {
  const p = path.join(DATA_DIR, "portfolios", agentId, "snapshots.json");
  if (!fs.existsSync(p)) return [];
  // Snapshots files may contain bare `NaN` tokens (not valid JSON) in position
  // sub-fields. Replace them with null before parsing so JSON.parse doesn't throw.
  const text = fs.readFileSync(p, "utf-8").replace(/:\s*NaN/g, ": null");
  const raw = JSON.parse(text) as PortfolioSnapshot[];
  // Snapshots can carry duplicate dates (verified against satoshi/snapshots.json).
  // Keep the last occurrence per date.
  const byDate = new Map<string, PortfolioSnapshot>();
  for (const s of raw) byDate.set(s.date, s);
  return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
}

/**
 * Per-position weights, or `null` for every row when the book mixes currencies.
 *
 * The weight column divided each position's value by the sum of all of them.
 * That sum is only meaningful when every value is in the same currency, and
 * `world` holds CHF, EUR and GBP at once — so one foreign position made every
 * row's weight wrong, not just its own (2026-08-07 review, W7.2). This is the
 * fourth appearance of the cross-currency-sum class in this codebase.
 *
 * The site has no FX conversion and is not getting one: `engine/fx.py` is the
 * single implementation, and adding a TypeScript copy of a currency rule is
 * exactly what the quote-currency defect was made of. So a mixed-currency book
 * shows no weights rather than plausible wrong ones. Unvalued rows (no price)
 * are excluded from the denominator but do not, on their own, suppress the
 * column — they are a gap, not an incoherent unit.
 */
export function positionWeights(
  rows: { value: number | null; currency: string | null }[],
): (number | null)[] {
  const valued = rows.filter((r) => r.value !== null);
  const currencies = new Set(valued.map((r) => r.currency ?? "?"));
  if (currencies.size > 1) return rows.map(() => null);

  const total = valued.reduce((acc, r) => acc + (r.value as number), 0);
  if (total <= 0) return rows.map(() => null);
  return rows.map((r) => (r.value === null ? null : (r.value / total) * 100));
}

/** True when a book's valued positions span more than one currency. */
export function isMixedCurrency(
  rows: { value: number | null; currency: string | null }[],
): boolean {
  const valued = rows.filter((r) => r.value !== null);
  return new Set(valued.map((r) => r.currency ?? "?")).size > 1;
}
