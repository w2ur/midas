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

/** Read a numeric data attribute. Anything not finite reads back as null. */
export function parseValue(raw: string | null | undefined): number | null {
  if (raw === null || raw === undefined) return null;
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}
