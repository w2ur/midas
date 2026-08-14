/**
 * Ordering logic for the leaderboard board.
 *
 * Pure by design: no DOM, no I/O, no module state. The component script in
 * LeaderboardTable.astro reads data attributes, calls orderRows, and does
 * nothing but move nodes — so every ordering rule is unit-testable here.
 */

export type SortKey = "return" | "vsBench" | "vsCoin";
export type SortDir = "desc" | "asc";

export interface SortRec {
  agent: string;
  return_pct: number;
  /** Delta vs the agent's own passive benchmark; null when no baseline exists. */
  vsBench: number | null;
  /** Delta vs the agent's coin-flip control; null when no baseline exists. */
  vsCoin: number | null;
}

const VALUE: Record<SortKey, (r: SortRec) => number | null> = {
  return: (r) => r.return_pct,
  vsBench: (r) => r.vsBench,
  vsCoin: (r) => r.vsCoin,
};

/**
 * Return agent ids in display order.
 *
 * Nulls sort last in BOTH directions: a missing baseline is an absence of
 * information, and must never be rendered as the worst — or the best — score.
 * Among themselves, null rows order by raw EUR return (descending, whatever
 * `dir` says) — the exact rule `engine.leaderboard.rank_leaderboard_rows`
 * applies to its own null tail, so a client re-sort can never renumber rows
 * the engine already ranked. Ties break by agent id so the order never
 * shuffles between renders.
 *
 * The MSCI World reference row is not an agent and is never passed in; the
 * caller pins it to the bottom unconditionally.
 */
export function orderRows(recs: SortRec[], key: SortKey, dir: SortDir): string[] {
  const pick = VALUE[key];
  return [...recs]
    .sort((a, b) => {
      const va = pick(a);
      const vb = pick(b);
      if (va === null && vb === null) {
        if (a.return_pct !== b.return_pct) return b.return_pct - a.return_pct;
        return a.agent.localeCompare(b.agent);
      }
      if (va === null) return 1;
      if (vb === null) return -1;
      if (va !== vb) return dir === "desc" ? vb - va : va - vb;
      return a.agent.localeCompare(b.agent);
    })
    .map((r) => r.agent);
}

/** The subset of a leaderboard row the layout decision reads. */
export interface LayoutRec {
  vs_benchmark_pp?: number | null;
  fx_translation_pp?: number | null;
}

export interface BoardLayout {
  /**
   * True when the benchmark-relative excess is the row's headline figure —
   * the number the bar scales and the #1 phosphor marks.
   */
  leadIsExcess: boolean;
  /** True when the book-currency return and FX translation columns render. */
  showDecomp: boolean;
}

/**
 * Decide what the board leads with, from the data rather than from a caller's
 * assertion.
 *
 * The board has ranked on `vs_benchmark_pp` since 2026-08-14 while still
 * leading with the EUR return, so its #1 row read -9.43% above nine rows that
 * looked better. The ranked quantity has to BE the headline or the board
 * argues with itself — but only where that quantity exists: bundles published
 * before that date carry no `vs_benchmark_pp`, and re-leading them on a metric
 * that did not exist when they were published would restate history in the
 * reader's eye. Same era-awareness as the `initialSort` "artifact" default and
 * as `scripts.restate_bundles`.
 *
 * `some`, not `every`: one agent without a baseline series ranks null-last and
 * renders "—", which is not a reason to demote the whole board.
 *
 * The decomposition pair says nothing on an all-EUR desk, where
 * `return_local_pct` equals `return_pct` by construction and every FX cell is
 * "—". It is keyed on the FX leg because that is exactly the condition under
 * which the two returns differ.
 */
export function boardLayout(rows: LayoutRec[], showVsBench: boolean): BoardLayout {
  const leadIsExcess =
    showVsBench && rows.some((r) => r.vs_benchmark_pp !== null && r.vs_benchmark_pp !== undefined);
  const showDecomp =
    leadIsExcess &&
    rows.some((r) => r.fx_translation_pp !== null && r.fx_translation_pp !== undefined);
  return { leadIsExcess, showDecomp };
}

/** Read a numeric data attribute. Anything not finite reads back as null. */
export function parseValue(raw: string | null | undefined): number | null {
  if (raw === null || raw === undefined) return null;
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}
