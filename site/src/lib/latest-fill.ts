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
export function latestFill(orders: Order[], isTrader: (id: string) => boolean): Order | null {
  let best: Order | null = null;
  for (const o of orders) {
    if (o.status !== "filled" || !isTrader(o.agent_id)) continue;
    if (best === null || newer(o, best)) best = o;
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
