import type { Order } from "./orders";
import { formatPrice, longDate } from "./format";
import { inferCurrency } from "./ohlcv";
import { BROKER_RAILS, WATCHER_RAILS } from "./rails";

/**
 * Outcomes of a cancel request filed under a real order id: the request's
 * target was not pending, or belonged to another agent. rails.ts lists them
 * with the broker rails and does not group them, so the set is named once
 * here. They describe a cancel, not a refused trade, and never lead.
 */
const CANCEL_OUTCOME_CODES: ReadonlySet<string> = new Set(["CANCEL_TARGET_NOT_FOUND", "CANCEL_NOT_OWNER"]);

export type DecisionKind = "fill" | "refusal" | "pending" | "housekeeping";

export type Classification = {
  kind: DecisionKind;
  label: string;
  /** The reason code to print beside the label, when it adds information. */
  code: string | null;
};

/**
 * What a record is, from its status and reason code: the single source for
 * both the hero's pick (latestDecision) and its copy (statusLine). The status
 * alone cannot say it: a cancellation, an expiry and a cancel outcome are all
 * status "rejected". Housekeeping (the agent withdrawing its own pending
 * order, the watcher expiring one, a cancel outcome) is derived from rails.ts
 * where it groups the codes (WATCHER_RAILS), and a pending record with no
 * trigger, never lead.
 */
export function classify(o: Order, today: Date = new Date()): Classification {
  if (o.status === "filled") return { kind: "fill", label: "Filled by the paper broker", code: null };
  if (o.status !== "rejected") {
    // Pending only means no inbox row is filed under this id. Only a trigger
    // makes it an armed order. Without one (an INVALID_ORDER_ID refusal filed
    // under a derived id, a session push before the broker ran for a market
    // order, a fire on an unmerged triggers/* branch) it is merely unresolved:
    // housekeeping, so it never leads, though the archive still labels it.
    if (!o.trigger) return { kind: "housekeeping", label: "Not yet resolved", code: null };
    // The 13:00 UTC sweep writes TRIGGER_EXPIRED only after the expiry day has
    // passed (inclusive), so until then the row still reads pending. An order
    // whose expiry is before the build day is already dead: housekeeping.
    if (expired(o, today)) return { kind: "housekeeping", label: "Expired before it fired", code: null };
    return { kind: "pending", label: "Armed with the paper broker, waiting for its trigger", code: null };
  }
  const code = o.reject_reason || null;
  if (code === "CANCELLED_BY_AGENT") return { kind: "housekeeping", label: "Withdrawn by the agent", code: null };
  if (code && WATCHER_RAILS.some((r) => r.code === code)) return { kind: "housekeeping", label: "Expired before it fired", code };
  if (code && CANCEL_OUTCOME_CODES.has(code)) return { kind: "housekeeping", label: "Cancel outcome", code };
  if (code && BROKER_RAILS.some((r) => r.code === code)) return { kind: "refusal", label: "Refused by the paper broker", code };
  return { kind: "refusal", label: "Rejected", code };
}

/**
 * The record's moment as an ISO string: the broker's confirmation, else the
 * moment the agent wrote it, whichever first parses; null when neither does.
 * A garbled or missing stamp must degrade the card, never throw while the
 * front page renders and fail the deploy.
 */
function recordTime(o: Order): string | null {
  for (const iso of [o.ts_filled, o.ts_written]) {
    if (typeof iso === "string" && /^\d{4}-\d{2}-\d{2}/.test(iso) && !Number.isNaN(Date.parse(iso))) return iso;
  }
  return null;
}

/** True when the order's inclusive expiry day (ISO date) is before the build's UTC day. */
function expired(o: Order, today: Date): boolean {
  return !!o.expires && o.expires < today.toISOString().slice(0, 10);
}

/**
 * The newest trader record that passes `keep`, under the ledger's ordering.
 * The Manager is excluded by the caller's predicate: its channel is isolated
 * and unranked, and it does not lead the front page.
 */
function newestTrader<Id extends string>(
  orders: Order[],
  isTrader: (id: string) => id is Id,
  keep: (o: Order) => boolean = () => true,
): (Order & { agent_id: Id }) | null {
  let best: (Order & { agent_id: Id }) | null = null;
  for (const o of orders) {
    if (!isTrader(o.agent_id) || !keep(o)) continue;
    const rec = o as Order & { agent_id: Id };
    if (best === null || newer(rec, best)) best = rec;
  }
  return best;
}

/**
 * The newest broker-confirmed fill by any trading agent, or null when no
 * trader has a fill. Ranked by the one key every surface shares (sortKey):
 * `${confirmed}|${ts_filled ?? ts_written}|${order_id}`, where a filled row
 * without `ts_filled` is unconfirmed (confirmed = 0) and so ranks behind every
 * broker-confirmed fill whatever its `ts_written`. "Newest" is therefore by
 * confirmation time, not by the date the order was authored: a conditional
 * order written weeks ago that fired today is today's fill. An unconfirmed
 * fill is returned (newest `ts_written`, then order_id) only when no trader
 * has a confirmed one.
 *
 * This is the fill-only contract of the hub endpoint (/latest-fill.json); the
 * front page uses latestDecision, which takes a record of any status under
 * the same key (an unresolved record ranks by `ts_written`).
 */
export function latestFill<Id extends string>(
  orders: Order[],
  isTrader: (id: string) => id is Id,
): (Order & { agent_id: Id }) | null {
  return newestTrader(orders, isTrader, (o) => o.status === "filled");
}

/**
 * The record the front page's "Latest decision" card leads with: the newest
 * trader decision (a fill, a genuine broker refusal, or a still-armed
 * pending order, one that carries a trigger), so a refusal newer than the last fill leads. Housekeeping rows (the
 * agent cancelling its own pending order, the watcher expiring one) are
 * skipped: they are stamped when the cleanup ran, often seconds after the
 * session's real decision, and describe an old order, not a new choice. Same
 * ordering as latestFill (resolution time); the Manager stays excluded on
 * both. Null only when no such record exists.
 */
export function latestDecision<Id extends string>(
  orders: Order[],
  isTrader: (id: string) => id is Id,
  today: Date = new Date(),
): (Order & { agent_id: Id }) | null {
  return newestTrader(orders, isTrader, (o) => classify(o, today).kind !== "housekeeping");
}

/**
 * The card header's timestamp: the broker's confirmation for a resolved
 * record, the moment the agent wrote it for a pending one (the same key
 * sortKey orders by), the bare authored day when neither exists. The stamp
 * is always a real moment: its day is the timestamp's own UTC day, matching
 * its `datetime`, even when the record is filed under another day's outbox
 * file; statusLine's "order written" note explains that file.
 */
export function decisionStamp(o: Order): { datetime: string; text: string } {
  const iso = recordTime(o);
  if (!iso) return { datetime: o.date, text: longDate(o.date) };
  return { datetime: iso, text: `${longDate(iso.slice(0, 10))}, ${new Date(iso).toISOString().slice(11, 16)} UTC` };
}

export type StatusLine = {
  label: string;
  /** The broker's refusal code, for a refused record that carries one. */
  code: string | null;
  /** Long date the order was written, when that differs from its resolution day. */
  writtenOn: string | null;
};

/**
 * The card's status copy. "Order written <day>" is there to explain why the
 * "Raw order record" link opens an earlier day's outbox file, so it shows
 * exactly when the UTC date of the file it is filed in (`date`, what the note
 * prints) differs from the UTC date of the header stamp (resolution time, or
 * `ts_written` for an unresolved record). Elapsed time plays no part:
 * an order written at 20:12 and filled at 02:48 the next UTC day lives in one
 * day's file and resolves in the next, and the note must say so; an order
 * resolved seconds after writing on the same day must not carry one. Refused
 * and pending-with-a-resolution-date records follow the same rule.
 */
export function statusLine(o: Order, today: Date = new Date()): StatusLine {
  const { label, code } = classify(o, today);
  return { label, code, writtenOn: waited(o) ? longDate(o.date) : null };
}

/**
 * What an armed order waits for, for the price slot of a pending card:
 * "fires at \u2264 USD 85.50 \u00b7 until 20 November 2026". The trigger level is
 * in the ticker's quote currency (the order's own `currency` is the book's, EUR
 * for a USD stock), so the currency comes from the ticker. Null for a resolved
 * record, one past its expiry (the build day, UTC, is `today`) and one with no trigger: it is not armed, so there is no
 * condition to print (its expiry alone would suggest otherwise).
 */
export function pendingCondition(o: Order, today: Date = new Date()): string | null {
  if (o.status !== "pending" || !o.trigger || expired(o, today)) return null;
  const op = o.trigger.op === "<=" ? "\u2264" : o.trigger.op === ">=" ? "\u2265" : o.trigger.op;
  const parts = [`fires at ${op} ${formatPrice(o.trigger.level, inferCurrency(o.ticker))}`];
  if (o.expires) parts.push(`until ${longDate(o.expires)}`);
  return parts.join(" \u00b7 ");
}

function waited(o: Order): boolean {
  // The note prints o.date and the raw link opens outbox/<o.date>.jsonl, so it
  // is the file's day the stamp's UTC day is compared with: the resolution
  // time for a resolved record, ts_written for one that is not (a few rows
  // are stamped on a different UTC day from their file).
  const iso = recordTime(o);
  return iso ? iso.slice(0, 10) !== o.date : false;
}

/** Newest confirmation first, unconfirmed fills last — the tape's order. The hero and the hub endpoint read the same key (sortKey). */
export function byFillTimeDesc(a: Order, b: Order): number {
  const ka = sortKey(a);
  const kb = sortKey(b);
  return ka === kb ? 0 : ka > kb ? -1 : 1;
}

/**
 * The one ranking key (tape, hero and hub endpoint alike):
 * `${confirmed}|${time}|${order_id}`. `time` is the broker's confirmation, else
 * the moment the agent wrote the record, else the start of its authored day (also when the stamps do not parse).
 * `confirmed` is 0 only for a filled row with no `ts_filled`, an unconfirmed
 * fill that ranks behind every other record; every other record (a confirmed
 * fill, a refusal, a pending order) ranks by its own time. ISO timestamps
 * compare lexically.
 */
function sortKey(o: Order): string {
  const unconfirmedFill = o.status === "filled" && !o.ts_filled;
  return `${unconfirmedFill ? 0 : 1}|${recordTime(o) ?? `${o.date}T00:00:00Z`}|${o.order_id}`;
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
