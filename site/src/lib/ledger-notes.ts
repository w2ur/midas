import type { AgentId } from "./roster";

/**
 * Known, deliberately-unreconciled ledger artifacts, keyed by agent id.
 *
 * Midas's whole point is that the ledger is not edited after the fact — see
 * the "the ledger is not being rewritten" changelog entry in METHODOLOGY.md
 * (2026-07-27). When an infrastructure bug still leaves a real distortion in
 * a reported return, the fix is disclosure, not a silent data rewrite. This
 * module is the single source of truth for that disclosure, so the note
 * renders on every page that shows the affected number instead of being
 * hand-typed into JSX in more than one place — and if an incident is ever
 * reconciled, deleting its entry here removes the marker from the whole site
 * in the same commit that fixes the ledger.
 *
 * `coherentReplayReturnPct` is a hand-authored, permanently fixed figure —
 * it is the return under a hypothetical replay that never happened, so it
 * cannot be derived from committed data the way every other return on this
 * site is. The *current* reported return is intentionally NOT duplicated
 * here: callers read it from the live `bundle`/`leaderboard` data they
 * already have, so this note can never drift out of sync with the number it
 * sits next to.
 */

export interface LedgerNote {
  /** The order whose confirmed fill never reached the portfolio ledger. */
  orderId: string;
  /** Short, page-safe label for a compact marker (leaderboard tile, badge). */
  label: string;
  /** Stand-alone paragraph explaining the artifact, for the agent's own dossier. */
  summary: string;
  /** Return (%) under a coherent replay that inserts the lost fill and voids
   *  the later sale it invalidates. See METHODOLOGY.md for the full replay. */
  coherentReplayReturnPct: number;
  /** In-page anchor on /methodology carrying the full incident record. */
  methodologyHref: string;
}

export const LEDGER_NOTES: Partial<Record<AgentId, LedgerNote>> = {
  "sharp-shooter-eur": {
    orderId: "ord_2026-05-21_sharp-shooter-eur_001",
    label: "Known ledger artifact",
    summary:
      "A confirmed SELL 1 ASML.AS @ €1249 (fired 2026-05-21T21:47:43Z) never reached this " +
      "portfolio's ledger — an infrastructure bug in the trigger watcher's commit step " +
      "dropped the portfolio write while the broker's fill confirmation was pushed. The " +
      "return shown above is exactly as executed; it has not been edited to correct this. A " +
      "coherent replay — inserting the lost sale and voiding the later sale it invalidates " +
      "— returns +0.28% instead.",
    coherentReplayReturnPct: 0.28,
    methodologyHref: "/methodology#lost-fill-2026-05-21",
  },
};

/** Returns the ledger note for an agent id, or null if it carries none. */
export function getLedgerNote(agentId: string): LedgerNote | null {
  return LEDGER_NOTES[agentId as AgentId] ?? null;
}
