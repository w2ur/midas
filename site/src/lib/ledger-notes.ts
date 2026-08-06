import type { AgentId } from "./roster";

/**
 * Known ledger incidents worth surfacing on an agent's own dossier, keyed by
 * agent id. Not every entry describes an active distortion — an incident
 * stays listed after it is reconciled, because the marker's job is
 * disclosure of what happened to the number, not just a flag on a number
 * that is currently wrong. This module is the single source of truth for
 * that disclosure, so the note renders on every page that shows the
 * affected number instead of being hand-typed into JSX in more than one
 * place — remove an entry only when the incident is no longer worth a
 * reader's attention at all, not merely once it has been fixed.
 *
 * The *current* reported return is intentionally NOT duplicated here:
 * callers read it from the live `bundle`/`leaderboard` data they already
 * have, so this note can never drift out of sync with the number it sits
 * next to.
 */

export interface LedgerNote {
  /** The order whose confirmed fill never reached the portfolio ledger. */
  orderId: string;
  /** Short, page-safe label for a compact marker (leaderboard tile, badge). */
  label: string;
  /** Stand-alone paragraph explaining the artifact, for the agent's own dossier. */
  summary: string;
  /** In-page anchor on /methodology carrying the full incident record. */
  methodologyHref: string;
}

export const LEDGER_NOTES: Partial<Record<AgentId, LedgerNote>> = {
  "sharp-shooter-eur": {
    orderId: "ord_2026-05-21_sharp-shooter-eur_001",
    label: "Ledger artifact — reconciled 2026-08-02",
    summary:
      "A confirmed SELL 1 ASML.AS @ €1249 (fired 2026-05-21T21:47:43Z) never reached this " +
      "portfolio's ledger — an infrastructure bug in the trigger watcher's commit step " +
      "dropped the portfolio write while the broker's fill confirmation was pushed. The book " +
      "therefore still showed a share it had sold, and on 2026-06-24 the agent sold that same " +
      "share again — a sale that could not have happened. On 2026-08-02 the ledger was " +
      "reconciled: the lost sale was inserted, the 2026-06-24 sale it invalidated was voided, " +
      "and the return shown above already reflects the corrected book.",
    methodologyHref: "/methodology#lost-fill-2026-05-21",
  },
};

/** Returns the ledger note for an agent id, or null if it carries none. */
export function getLedgerNote(agentId: string): LedgerNote | null {
  return LEDGER_NOTES[agentId as AgentId] ?? null;
}
