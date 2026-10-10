import type { Order } from "./orders";

/**
 * The fill the front page leads with: the newest broker-confirmed fill by any
 * of the trading agents. "Newest" is by confirmation time, not by the date the
 * order was authored — a conditional order written weeks ago that fired today
 * is today's fill. Orders without a confirmation time sort by authored date,
 * then order_id, behind every timestamped fill on the same terms.
 *
 * Never hides: a day with no fills shows the newest earlier one, because the
 * card is proof that decisions are public, not a claim about today.
 * The Manager is excluded by the caller's predicate — its channel is isolated
 * and unranked, and it does not lead the front page.
 */
export function latestFill<Id extends string>(
  orders: Order[],
  isTrader: (id: string) => id is Id,
): (Order & { agent_id: Id }) | null {
  let best: (Order & { agent_id: Id }) | null = null;
  for (const o of orders) {
    if (o.status !== "filled" || !isTrader(o.agent_id)) continue;
    const fill = o as Order & { agent_id: Id };
    if (best === null || newer(fill, best)) best = fill;
  }
  return best;
}

/** Newest confirmation first — the order the hero and the tape both read. */
export function byFillTimeDesc(a: Order, b: Order): number {
  const ka = sortKey(a);
  const kb = sortKey(b);
  return ka === kb ? 0 : ka > kb ? -1 : 1;
}

function sortKey(o: Order): string {
  // ISO timestamps compare lexically; an unconfirmed time is pinned to the
  // start of its authored day so it sorts before any timestamp on that day.
  return `${o.ts_filled ?? `${o.date}T00:00:00Z`}|${o.order_id}`;
}

function newer(a: Order, b: Order): boolean {
  return sortKey(a) > sortKey(b);
}

/**
 * Address of an order's raw outbox line in the public repository, by its
 * physical line on main. Not pinned to the fill's `executed_sha`: a session's
 * broker runs before the session commits its outbox, so for an ordinary fill
 * that commit's tree does not contain the order yet (measured 2026-10-10 —
 * only conditional fires, authored in an earlier session, resolve there).
 */
export function rawRecordUrl(o: Order): string {
  const base = `https://github.com/w2ur/midas/blob/main/data/orders/outbox/${o.date}.jsonl`;
  return o.line ? `${base}#L${o.line}` : base;
}

/** The latest fill as william.revah.paris receives it at its build time. */
export type HubFill = {
  agent_id: string;
  agent: string;
  action: "BUY" | "SELL";
  ticker: string;
  shares: number;
  price: number | null;
  currency: string | null;
  /** ISO timestamp of the broker's confirmation, or null if unrecorded. */
  filled_at: string | null;
  /** The date the agent wrote the order; differs from filled_at for a conditional order. */
  written_on: string;
  /** The agent's rationale, verbatim. The consumer decides how much to show. */
  reasoning: string;
  dossier_url: string;
  record_url: string;
};

/**
 * Projects a fill onto the hub's contract. Raw numbers, not formatted strings:
 * the hub renders in French and English and formats for its own locale.
 * `site` is astro.config's `site` (the endpoint's `context.site`), so the
 * dossier link follows the canonical host rather than a copy of it.
 */
export function toHubFill(o: Order, agentName: string, site: URL): HubFill {
  return {
    agent_id: o.agent_id,
    agent: agentName,
    action: o.action,
    ticker: o.ticker,
    shares: o.shares,
    price: o.fill_price,
    currency: o.fill_currency,
    filled_at: o.ts_filled ?? null,
    written_on: o.date,
    reasoning: o.reasoning,
    dossier_url: new URL(`/arena/${o.agent_id}`, site).href,
    record_url: rawRecordUrl(o),
  };
}
