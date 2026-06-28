/**
 * Baseline-chart legend ordering.
 *
 * Returns the chart series that are actually present, in canonical draw order
 * ["agent", "benchmark", "coinflip"]. The dossier chart uses this for BOTH the
 * drawn lines and the legend so a missing baseline/coin-flip series is never
 * advertised by an orphan legend entry (the bug this fixes).
 */
export type BaselineSeries = "agent" | "benchmark" | "coinflip";

const ORDER: BaselineSeries[] = ["agent", "benchmark", "coinflip"];

export function legendEntries(present: {
  agent: boolean;
  benchmark: boolean;
  coinflip: boolean;
}): string[] {
  return ORDER.filter((key) => present[key]);
}
