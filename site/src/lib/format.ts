/**
 * A fill price as a reader expects to see money: grouped thousands, the ISO
 * code in front, two decimals (four under 10, where FX and penny prices live).
 * The tape printed `EUR 73348.92`; this prints `EUR 73,348.92`. No currency
 * symbol on purpose — the code is what the ledger records, and a symbol would
 * guess between `$` and `US$`. Never converts: the site has no FX arithmetic.
 */
export function formatPrice(n: number | null, ccy: string | null): string {
  if (n === null || !Number.isFinite(n)) return "—";
  const digits = Math.abs(n) < 10 ? 4 : 2;
  const num = new Intl.NumberFormat("en-GB", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n);
  return ccy ? `${ccy} ${num}` : num;
}

/** A share count without trailing zeros: whole above 100, 2 dp above 1, 4 dp below. */
export function formatShares(n: number): string {
  if (Math.abs(n) >= 100) return n.toFixed(0);
  if (Math.abs(n) >= 1) return n.toFixed(2).replace(/\.?0+$/, "");
  return n.toFixed(4).replace(/\.?0+$/, "");
}

/** `2026-10-10` → `10 October 2026`, read in UTC so the day never shifts. */
export function longDate(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}
