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

/**
 * A share count as traded: up to 8 decimals (a crypto fill can be 0.00004),
 * trailing zeros dropped, never rounded to fewer digits than it has. The old
 * banding printed 150.5 as "151" and 0.00004 BTC as "0".
 */
export function formatShares(n: number): string {
  return n.toLocaleString("en-GB", { maximumFractionDigits: 8, useGrouping: false });
}

/** `2026-10-10` → `10 October 2026`, read in UTC so the day never shifts. */
export function longDate(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}
