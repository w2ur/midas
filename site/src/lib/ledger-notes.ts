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
  /**
   * The single order at the heart of the incident, where there is one.
   * Omitted when the incident spans several fills (the 2026-08-07 quote-
   * currency reconciliation touched up to 13 in one book), in which case the
   * count belongs in `summary` and the enumeration on /methodology.
   */
  orderId?: string;
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
    methodologyHref: "/methodology/changelog#lost-fill-2026-05-21",
  },
  world: {
    orderId: "ord_2026-08-05_world_001",
    label: "Ledger artifact — reconciled 2026-08-07",
    summary:
      "The London Stock Exchange quotes in pence, and the broker read those pence as pounds. " +
      "This book's BUY of 8 LLOY.L at 116.60 (2026-08-05) was therefore paid for at €1,090.19 " +
      "instead of €10.90 — a factor of 100 — and every valuation after it carried the same " +
      "error. On 2026-08-07 the fill was reconciled and €1,079.29 of cash restored; the return " +
      "shown above already reflects the corrected book.",
    methodologyHref: "/methodology/changelog#sweep-and-restatement-2026-08-07",
  },
  goldfinger: {
    label: "Ledger artifact — reconciled 2026-08-07",
    summary:
      "A ticker's currency used to be guessed from its suffix, and `.L` is not uniformly " +
      "sterling: this book's silver (PHAG.L) and oil (CRUD.L) quote in US dollars but were " +
      "bought and sold as though they quoted in pounds. Nine fills between 2026-04-20 and " +
      "2026-08-07 moved the wrong amount of cash. On 2026-08-07 they were reconciled and " +
      "€873.26 restored; the return shown above already reflects the corrected book.",
    methodologyHref: "/methodology/changelog#sweep-and-restatement-2026-08-07",
  },
  "monsieur-forex": {
    label: "Ledger artifact — reconciled 2026-08-07",
    summary:
      "A currency pair is quoted in its second leg — EURJPY=X in yen, USDCAD=X in Canadian " +
      "dollars, USDCHF=X in francs, EURGBP=X in sterling — but the broker converted all four " +
      "as US dollars. Thirteen fills between 2026-05-18 and 2026-08-05 moved the wrong amount " +
      "of cash, the largest by a factor of about 160. On 2026-08-07 they were reconciled and " +
      "€21.98 restored — small only because the errors ran in both directions and largely " +
      "cancelled; the return shown above already reflects the corrected book.",
    methodologyHref: "/methodology/changelog#sweep-and-restatement-2026-08-07",
  },
};

/** Returns the ledger note for an agent id, or null if it carries none. */
export function getLedgerNote(agentId: string): LedgerNote | null {
  return LEDGER_NOTES[agentId as AgentId] ?? null;
}
