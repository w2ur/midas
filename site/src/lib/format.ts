/**
 * A fill price as a reader expects to see money: grouped thousands, the ISO
 * code in front, two decimals from 10, four from 1, four significant below 1.
 * The tape printed `EUR 73348.92`; this prints `EUR 73,348.92`. No currency
 * symbol on purpose — the code is what the ledger records, and a symbol would
 * guess between `$` and `US$`. Never converts: the site has no FX arithmetic.
 */
export function formatPrice(n: number | null, ccy: string | null): string {
  if (n === null || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  // Two decimals from 10, four from 1, four significant digits below 1 so a
  // sub-cent token never prints as a price of zero. Decided on the rounded
  // value, so 9.99996 prints 10.00 and 0.99996 prints 1.0000.
  const opts: Intl.NumberFormatOptions =
    abs > 0 && Number(abs.toPrecision(4)) < 1
      ? { maximumSignificantDigits: 4, minimumSignificantDigits: 4 }
      : Number(abs.toFixed(4)) < 10
        ? { minimumFractionDigits: 4, maximumFractionDigits: 4 }
        : { minimumFractionDigits: 2, maximumFractionDigits: 2 };
  const num = new Intl.NumberFormat("en-GB", opts).format(n);
  return ccy ? `${ccy} ${num}` : num;
}

/**
 * A share count: six significant digits, trailing zeros dropped, never
 * rounded to zero. Size bands printed 150.5 as "151" and a 0.00004 BTC fill as
 * "0"; a fixed 8 decimals printed the Manager's notional/price quantities as
 * float tails (2.60575008). Six significant digits keep every traded figure and
 * drop the noise. Negative zero prints as 0.
 */
export function formatShares(n: number): string {
  if (n === 0) return "0";
  // Never fewer significant digits than the integer part has: 1234567 stays whole.
  const intDigits = Math.abs(n) >= 1 ? Math.floor(Math.log10(Math.abs(n))) + 1 : 0;
  return n.toLocaleString("en-GB", {
    maximumSignificantDigits: Math.min(21, Math.max(6, intDigits)),
    useGrouping: false,
  });
}

/** `2026-10-10` → `10 October 2026`, read in UTC so the day never shifts. */
export function longDate(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}
