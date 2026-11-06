import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { latestFill, latestDecision, statusLine, classify, decisionStamp, pendingCondition, rawRecordUrl, byFillTimeDesc } from "@/lib/latest-fill";
import { formatPrice, formatShares, longDate } from "@/lib/format";
import { DATA_DIR } from "@/lib/paths";
import { loadAllOrders, type Order } from "@/lib/orders";
import { isTradingAgent } from "@/lib/roster";
import { BROKER_RAILS, WATCHER_RAILS } from "@/lib/rails";

function order(p: Partial<Order>): Order {
  return {
    order_id: "ord_x",
    agent_id: "satoshi",
    date: "2026-10-01",
    action: "BUY",
    ticker: "BTC-EUR",
    shares: 1,
    reasoning: "r",
    currency: "EUR",
    status: "filled",
    fill_price: 1,
    fill_currency: "EUR",
    fees: 0,
    reject_reason: null,
    ts_filled: null,
    ...p,
  };
}

// The production predicate, so the tests exercise the same narrowing.
const traders = isTradingAgent;

describe("latestFill", () => {
  it("leads with the newest confirmation, not the newest authored date", () => {
    // A conditional order authored weeks earlier that fired today is today's fill.
    const old = order({ order_id: "a", date: "2026-09-16", ts_filled: "2026-10-10T17:41:10Z" });
    const recent = order({ order_id: "b", date: "2026-10-09", ts_filled: "2026-10-09T22:30:00Z" });
    expect(latestFill([recent, old], traders)?.order_id).toBe("a");
  });

  it("ignores rejected and pending orders", () => {
    const fill = order({ order_id: "f", ts_filled: "2026-10-01T22:00:00Z" });
    const rej = order({ order_id: "r", status: "rejected", ts_filled: "2026-10-05T22:00:00Z" });
    const pen = order({ order_id: "p", status: "pending", date: "2026-10-06" });
    expect(latestFill([rej, fill, pen], traders)?.order_id).toBe("f");
  });

  it("excludes agents the predicate rejects (the Manager)", () => {
    const mgr = order({ order_id: "m", agent_id: "the-manager", ts_filled: "2026-10-10T22:00:00Z" });
    const t = order({ order_id: "t", ts_filled: "2026-10-01T22:00:00Z" });
    expect(latestFill([mgr, t], traders)?.order_id).toBe("t");
  });

  it("falls back to an earlier fill rather than hiding, and is null with no fills", () => {
    expect(latestFill([order({ order_id: "only", date: "2026-04-17" })], traders)?.order_id).toBe("only");
    expect(latestFill([order({ status: "rejected" })], traders)).toBeNull();
    expect(latestFill([], traders)).toBeNull();
  });

  it("an unconfirmed time sorts before a confirmed one on the same day", () => {
    const bare = order({ order_id: "z", date: "2026-10-10", ts_filled: null });
    const timed = order({ order_id: "a", date: "2026-10-10", ts_filled: "2026-10-10T01:00:00Z" });
    expect(latestFill([bare, timed], traders)?.order_id).toBe("a");
  });

  // Real shape: a filled row whose inbox line carries no ts_filled falls back
  // to ts_written in sortKey, which would put an unconfirmed fill ahead of a
  // broker-confirmed one. The hub wants the newest CONFIRMED fill.
  it("a filled row without ts_filled never beats a timestamped fill, even if written later", () => {
    const confirmed = order({ order_id: "ord_2026-11-05_world_001", agent_id: "world", date: "2026-11-05",
      ts_written: "2026-11-05T15:50:00.1Z", ts_filled: "2026-11-05T16:02:09.266401Z" });
    const bare = order({ order_id: "ord_2026-11-06_world_001", agent_id: "world", date: "2026-11-06",
      ts_written: "2026-11-06T09:00:00.1Z", ts_filled: null });
    expect(latestFill([bare, confirmed], traders)?.order_id).toBe("ord_2026-11-05_world_001");
    // One key everywhere: the unconfirmed fill ranks behind the confirmed one in the hero too.
    expect(latestDecision([bare, confirmed], traders)?.order_id).toBe("ord_2026-11-05_world_001");
    expect([bare, confirmed].sort(byFillTimeDesc).map((x) => x.order_id))
      .toEqual(["ord_2026-11-05_world_001", "ord_2026-11-06_world_001"]);
  });
  it("an unconfirmed fill still leads when no confirmed record exists, newest ts_written first", () => {
    const a = order({ order_id: "a", ts_written: "2026-11-06T09:00:00Z", ts_filled: null });
    const b = order({ order_id: "b", ts_written: "2026-11-06T10:00:00Z", ts_filled: null });
    expect(latestFill([a, b], traders)?.order_id).toBe("b");
    expect(latestDecision([a, b], traders)?.order_id).toBe("b");
  });
  it("a refusal or pending record still ranks by its own time against an unconfirmed fill", () => {
    const bare = order({ order_id: "bare", ts_written: "2026-11-06T09:00:00Z", ts_filled: null });
    const pen = order({ order_id: "p", status: "pending", trigger: { op: "<=", level: 10 }, ts_written: "2026-11-06T10:00:00Z", ts_filled: null });
    expect(latestDecision([bare, pen], traders)?.order_id).toBe("p");
  });
  it("finds a trader fill in the committed ledger, with a line address", () => {
    const o = latestFill(loadAllOrders(), isTradingAgent);
    expect(o).not.toBeNull();
    expect(o!.status).toBe("filled");
    expect(o!.reasoning.length).toBeGreaterThan(0);
    expect(rawRecordUrl(o!)).toMatch(/\/outbox\/\d{4}-\d{2}-\d{2}\.jsonl#L\d+$/);
  });
});

describe("latestDecision", () => {
  it("leads with the newest trader record of any status: a newer refusal beats an older fill", () => {
    const fill = order({ order_id: "f", ts_filled: "2026-10-01T22:00:00Z" });
    const rej = order({ order_id: "r", status: "rejected", reject_reason: "MAX_POSITION", ts_filled: "2026-10-05T22:00:00Z" });
    expect(latestDecision([fill, rej], traders)?.order_id).toBe("r");
    expect(latestDecision([rej, fill], traders)?.order_id).toBe("r");
  });

  it("leads with a fill when the fill is the newest record", () => {
    const fill = order({ order_id: "f", ts_filled: "2026-10-09T22:00:00Z" });
    const rej = order({ order_id: "r", status: "rejected", ts_filled: "2026-10-05T22:00:00Z" });
    expect(latestDecision([rej, fill], traders)?.order_id).toBe("f");
  });

  it("a newer pending record beats both a refusal and a fill", () => {
    const fill = order({ order_id: "f", date: "2026-10-06", ts_filled: "2026-10-06T22:07:40Z" });
    const rej = order({ order_id: "r", date: "2026-10-06", status: "rejected", reject_reason: "INSUFFICIENT_CASH", ts_filled: "2026-10-06T22:07:41Z" });
    const pen = order({ order_id: "p", status: "pending", trigger: { op: "<=", level: 10 }, date: "2026-10-06", ts_written: "2026-10-06T22:07:42Z" });
    expect(latestDecision([fill, rej, pen], traders)?.order_id).toBe("p");
    expect(latestDecision([rej], traders)?.order_id).toBe("r");
  });

  // Regression: a pending record was pinned to 00:00 UTC of its day, so it lost
  // to every resolution of its own session even when it was written last.
  it("a pending order sorts by when it was written, not by the start of its day", () => {
    const fill = order({ order_id: "f", date: "2026-11-04", ts_written: "2026-11-04T22:07:30Z", ts_filled: "2026-11-04T22:07:40Z" });
    const pen = order({ order_id: "p", status: "pending", trigger: { op: "<=", level: 10 }, date: "2026-11-04", ts_written: "2026-11-04T22:07:42Z", ts_filled: null });
    expect(latestDecision([fill, pen], traders)?.order_id).toBe("p");
    const earlier = order({ order_id: "q", status: "pending", trigger: { op: "<=", level: 10 }, date: "2026-11-04", ts_written: "2026-11-04T22:07:39Z", ts_filled: null });
    expect(latestDecision([fill, earlier], traders)?.order_id).toBe("f");
  });

  // Regression: the 2026-11-04 session cancelled stale orders written 2026-10-16;
  // the broker stamped those housekeeping rows 22:07:49, a hair after the
  // session's own fill, and they led the hero as "Refused by the paper broker".
  it("a cancellation of an old order does not beat the session's own fill", () => {
    const fill = order({ order_id: "ord_2026-11-04_steady-eddie-usd_001", date: "2026-11-04", ts_written: "2026-11-04T22:07:40Z", ts_filled: "2026-11-04T22:07:44.5Z" });
    const cancel = order({
      order_id: "ord_2026-10-16_steady-eddie-usd_002", date: "2026-10-16", ts_written: "2026-10-16T22:05:00Z",
      status: "rejected", reject_reason: "CANCELLED_BY_AGENT", fill_price: null, fill_currency: null, fees: null,
      ts_filled: "2026-11-04T22:07:49.119535Z",
    });
    expect(latestDecision([fill, cancel], traders)?.order_id).toBe(fill.order_id);
    expect(latestDecision([cancel, fill], traders)?.order_id).toBe(fill.order_id);
  });

  it("skips every watcher rail (an expiry is housekeeping) but keeps a genuine broker refusal", () => {
    const fill = order({ order_id: "f", ts_filled: "2026-10-01T22:00:00Z" });
    for (const code of WATCHER_RAILS.map((r) => r.code)) {
      const exp = order({ order_id: "e", status: "rejected", reject_reason: code, ts_filled: "2026-10-31T17:32:34Z" });
      expect(latestDecision([fill, exp], traders)?.order_id).toBe("f");
    }
    const refusal = order({ order_id: "r", status: "rejected", reject_reason: BROKER_RAILS[0].code, ts_filled: "2026-10-05T22:00:00Z" });
    expect(latestDecision([fill, refusal], traders)?.order_id).toBe("r");
  });

  // Regression: an INVALID_ORDER_ID refusal is filed under a derived id, so its
  // outbox row reads pending forever; a session push before the broker runs
  // does the same for a market order. Neither is armed, neither may lead.
  it("a pending record without a trigger never leads, however new", () => {
    const fill = order({ order_id: "f", ts_filled: "2026-11-04T22:07:40Z" });
    for (const trigger of [null, undefined]) {
      const unresolved = order({ order_id: "u", status: "pending", date: "2026-11-05", ts_written: "2026-11-05T09:00:00Z", ts_filled: null, trigger });
      expect(latestDecision([fill, unresolved], traders)?.order_id).toBe("f");
      expect(latestDecision([unresolved], traders)).toBeNull();
    }
  });

  it("is null when the only records are housekeeping", () => {
    const cancel = order({ status: "rejected", reject_reason: "CANCELLED_BY_AGENT", ts_filled: "2026-11-04T22:07:49Z" });
    expect(latestDecision([cancel], traders)).toBeNull();
  });

  it("never picks the Manager", () => {
    const mgr = order({ order_id: "m", agent_id: "the-manager", ts_filled: "2026-10-10T22:00:00Z" });
    const rej = order({ order_id: "r", status: "rejected", ts_filled: "2026-10-01T22:00:00Z" });
    expect(latestDecision([mgr, rej], traders)?.order_id).toBe("r");
    expect(latestDecision([mgr], traders)).toBeNull();
    expect(latestDecision([], traders)).toBeNull();
  });
});

describe("the rationale toggle", () => {
  const src = fs.readFileSync(path.join(__dirname, "../src/components/LatestFill.astro"), "utf-8");
  // Regression: main gated the toggle on a 110-character count with a
  // three-line clamp; the count cannot know how many lines a given font and
  // width produce, so a rationale could be cut with no toggle. The component
  // now always renders the <details> and a client script hides it only when
  // the clamped paragraph measurably fits.
  it("always renders the details, with no length gate", () => {
    expect(src).toMatch(/<details class="lf-more">/);
    expect(src).not.toMatch(/\{\s*clamps\s*&&/);
  });
  it("hides the toggle from a measurement, re-run on resize and after fonts load", () => {
    expect(src).toContain("scrollHeight");
    expect(src).toContain("clientHeight");
    expect(src).toContain('"resize"');
    expect(src).toContain("document.fonts.ready");
    expect(src).toMatch(/\.latest-fill\[data-fits\] \.lf-more\s*\{\s*display:\s*none/);
  });
});

describe("the rationale toggle does not shift the layout", () => {
  const src = fs.readFileSync(path.join(__dirname, "../src/components/LatestFill.astro"), "utf-8");
  // Regression: the toggle is server-rendered visible and was hidden only when
  // the bundled (deferred) module ran, so the hero shrank after first paint on
  // every short rationale. The first measurement is an inline script right
  // after the card, which runs before the next paint.
  it("is one inline script placed immediately after the card, with no bundled copy", () => {
    const m = src.match(/<\/aside>\s*<script is:inline>([\s\S]*?)<\/script>/);
    expect(m).not.toBeNull();
    expect(src.match(/<script[\s>]/g)).toHaveLength(1);
    expect(src.match(/scrollHeight/g)).toHaveLength(1);
    expect(m![1]).toContain("data-fits");
  });
  // Regression: fit() returns early while the card is open, and nothing
  // re-measured on close, so a resize while open left data-fits stale.
  it("re-measures when the details closes", () => {
    const body = src.match(/<script is:inline>([\s\S]*?)<\/script>/)![1];
    expect(body).toMatch(/more\.addEventListener\("toggle",[\s\S]*?!more\.open[\s\S]*?fit\(\)/);
  });
  // Regression: the hide decision compared scrollHeight with clientHeight of
  // the line-clamped box; where the standard line-clamp wins, lines past the
  // clamp may not be laid out, the heights can be equal, and the toggle hid
  // while text was cut. The text is now measured with the clamp lifted.
  it("measures the paragraph with the clamp lifted, not the clamped box's overflow", () => {
    const body = src.match(/<script is:inline>([\s\S]*?)<\/script>/)![1];
    expect(body).toMatch(/classList\.add\("lf-measure"\)[\s\S]*?scrollHeight[\s\S]*?classList\.remove\("lf-measure"\)/);
    expect(body).toMatch(/full\s*<=\s*clamped/);
    const rule = src.match(/\.lf-why\.lf-measure\s*\{([^}]*)\}/)![1];
    expect(rule).toMatch(/-webkit-line-clamp:\s*unset/);
    expect(rule).toMatch(/(?<![-\w])line-clamp:\s*none/);
    expect(rule).toMatch(/display:\s*block/);
  });
  it("measures immediately, then on font load and on a frame-throttled resize", () => {
    const body = src.match(/<script is:inline>([\s\S]*?)<\/script>/)![1];
    expect(body).toMatch(/fit\(\);\s*if \(document\.fonts/);
    expect(body).toContain("document.fonts.ready");
    expect(body).toMatch(/addEventListener\("resize"[\s\S]*requestAnimationFrame/);
  });
});

describe("the rationale wraps an unbreakable token", () => {
  // Regression: .lf-why and .lf-full set no overflow-wrap, so a 60-character
  // token (URL, ticker list) spilled past the card on a phone, and in the
  // clamped copy it stayed on one line and hid the toggle's reason to exist.
  const css = fs.readFileSync(path.join(__dirname, "../src/components/LatestFill.astro"), "utf-8");
  for (const cls of [".lf-why", ".lf-full"]) {
    it(`${cls} breaks anywhere`, () => {
      const body = css.match(new RegExp(`\\${cls}\\s*\\{([^}]*)\\}`, "g"))!.join("");
      expect(body).toMatch(/overflow-wrap:\s*anywhere/);
    });
  }
});

describe("statusLine", () => {
  it("says a fill was filled, and notes the authored day only when it differs", () => {
    expect(statusLine(order({ date: "2026-10-01", ts_filled: "2026-10-01T22:00:00Z" })))
      .toEqual({ label: "Filled by the paper broker", code: null, writtenOn: null });
    expect(statusLine(order({ date: "2026-09-16", ts_filled: "2026-10-10T17:41:10Z" })).writtenOn)
      .toBe(longDate("2026-09-16"));
  });
  it("names the refusal code, and the authored day when it was written earlier", () => {
    const same = statusLine(order({ status: "rejected", reject_reason: "INSUFFICIENT_CASH", date: "2026-10-05", ts_filled: "2026-10-05T22:00:00Z" }));
    expect(same).toEqual({ label: "Refused by the paper broker", code: "INSUFFICIENT_CASH", writtenOn: null });
    const later = statusLine(order({ status: "rejected", reject_reason: "INSUFFICIENT_CASH", date: "2026-09-16", ts_filled: "2026-10-05T22:00:00Z" }));
    expect(later.writtenOn).toBe(longDate("2026-09-16"));
  });
  // The note explains why "Raw order record" opens an earlier day's outbox
  // file, so it follows the UTC date of authoring versus resolution.
  // Regression: a 24-hour rule hid it for 115 ledger orders resolved the next
  // UTC day (e.g. ord_2026-06-04_satoshi_001, written 20:12, filled 02:48).
  it("notes the authored day when resolution falls on a later UTC date, even hours later", () => {
    const o = order({ date: "2026-06-04", ts_written: "2026-06-04T20:12:00Z", ts_filled: "2026-06-05T02:48:00Z" });
    expect(statusLine(o).writtenOn).toBe(longDate("2026-06-04"));
    // A session-start order resolved after midnight UTC is the same case.
    const late = order({ date: "2026-11-04", ts_written: "2026-11-04T22:07:42Z", ts_filled: "2026-11-05T00:03:10Z" });
    expect(statusLine(late).writtenOn).toBe(longDate("2026-11-04"));
  });
  it("notes nothing for an order resolved seconds after writing on the same UTC day", () => {
    const o = order({ date: "2026-11-04", ts_written: "2026-11-04T22:07:42Z", ts_filled: "2026-11-04T22:07:44.5Z" });
    expect(statusLine(o).writtenOn).toBeNull();
    // 23:59:59 to 00:00:01 is two seconds apart yet two outbox days.
    const edge = order({ date: "2026-11-04", ts_written: "2026-11-04T23:59:59Z", ts_filled: "2026-11-05T00:00:01Z" });
    expect(statusLine(edge).writtenOn).toBe(longDate("2026-11-04"));
  });
  it("notes the authored day for a conditional order fired days later", () => {
    const o = order({ date: "2026-10-27", ts_written: "2026-10-27T22:05:00Z", ts_filled: "2026-11-04T20:19:30Z" });
    expect(statusLine(o).writtenOn).toBe(longDate("2026-10-27"));
  });
  it("falls back to the session date when there is no authored timestamp", () => {
    expect(statusLine(order({ date: "2026-11-04", ts_filled: "2026-11-04T23:00:00Z" })).writtenOn).toBeNull();
    expect(statusLine(order({ date: "2026-11-04", ts_filled: "2026-11-05T00:03:10Z" })).writtenOn).toBe(longDate("2026-11-04"));
  });
  // Regression: waited() compared the UTC day of ts_written, but the note
  // prints o.date and the raw link opens outbox/<o.date>.jsonl. Six committed
  // rows are stamped on a different UTC day from their file; the note must
  // follow the file the link opens.
  it("follows the file the link opens when ts_written is stamped on another UTC day than the file", () => {
    const o = order({ date: "2026-05-22", ts_written: "2026-05-23T00:04:10Z", ts_filled: "2026-05-23T20:12:00Z" });
    expect(statusLine(o).writtenOn).toBe(longDate("2026-05-22"));
    expect(rawRecordUrl(o)).toContain("/outbox/2026-05-22.jsonl");
    // Resolved on the file's own day, whatever ts_written says: no note.
    const same = order({ date: "2026-05-22", ts_written: "2026-05-23T00:04:10Z", ts_filled: "2026-05-22T23:59:00Z" });
    expect(statusLine(same).writtenOn).toBeNull();
  });
  // Regression: the label read the status, so a cancellation or an expiry
  // (both status "rejected") was announced as a refusal by the broker.
  it("labels a rejection by its reason code, not its status", () => {
    const rej = (code: string | null) => statusLine(order({ status: "rejected", reject_reason: code, ts_filled: "2026-10-05T22:00:00Z" }));
    for (const r of BROKER_RAILS) {
      if (["CANCELLED_BY_AGENT", "CANCEL_TARGET_NOT_FOUND", "CANCEL_NOT_OWNER"].includes(r.code)) continue;
      expect(rej(r.code)).toMatchObject({ label: "Refused by the paper broker", code: r.code });
    }
    expect(rej("CANCELLED_BY_AGENT")).toMatchObject({ label: "Withdrawn by the agent", code: null });
    expect(rej("TRIGGER_EXPIRED")).toMatchObject({ label: "Expired before it fired", code: "TRIGGER_EXPIRED" });
    expect(rej("NOT_A_CODE")).toMatchObject({ label: "Rejected", code: "NOT_A_CODE" });
  });
  it("tolerates a refusal with no code", () => {
    expect(statusLine(order({ status: "rejected", reject_reason: null })).code).toBeNull();
  });
  // Pending only means no inbox row is filed under the id. A record with a
  // trigger is armed; one without (e.g. an INVALID_ORDER_ID refusal filed
  // under a derived id, or a fire on an unmerged branch) is just unresolved.
  it("says a pending order with a trigger is armed and waiting for it", () => {
    expect(statusLine(order({ status: "pending", ts_filled: null, trigger: { op: "<=", level: 10 } })))
      .toEqual({ label: "Armed with the paper broker, waiting for its trigger", code: null, writtenOn: null });
  });
  it("says a pending order without a trigger is not yet resolved, never armed", () => {
    for (const trigger of [null, undefined]) {
      expect(statusLine(order({ status: "pending", ts_filled: null, trigger })))
        .toEqual({ label: "Not yet resolved", code: null, writtenOn: null });
    }
  });
  // Same file/stamp split as a resolved record: the note explains the file the link opens.
  it("notes the file's day on a pending record written on another UTC day", () => {
    const pen = order({ order_id: "ord_2026-05-22_sharp-shooter-eur_001", status: "pending", date: "2026-05-22",
      ts_written: "2026-05-23T08:29:34.297684Z", ts_filled: null });
    expect(statusLine(pen).writtenOn).toBe(longDate("2026-05-22"));
    const same = order({ status: "pending", date: "2026-11-04", ts_written: "2026-11-04T22:07:42Z", ts_filled: null });
    expect(statusLine(same).writtenOn).toBeNull();
  });
  it("notes the authored day on a pending record whose resolution date differs", () => {
    expect(statusLine(order({ status: "pending", date: "2026-09-16", ts_filled: "2026-10-05T22:00:00Z" })).writtenOn)
      .toBe(longDate("2026-09-16"));
  });
});

// Shapes of the two committed trader rows (inbox 2026-05-29 and 2026-06-03):
// a cancel request filed under a real order id, resolved with this code.
const cancelNotFound = (id: string, date: string, ts: string) =>
  order({ order_id: id, agent_id: "sharp-shooter-usd", date, status: "rejected", reject_reason: "CANCEL_TARGET_NOT_FOUND",
    fill_price: null, fill_currency: null, fees: null, ts_filled: ts });

describe("classify", () => {
  const rej = (code: string | null) => classify(order({ status: "rejected", reject_reason: code }));
  it("labels every kind as specified", () => {
    expect(classify(order({ status: "filled" }))).toEqual({ kind: "fill", label: "Filled by the paper broker", code: null });
    expect(rej("INSUFFICIENT_CASH")).toEqual({ kind: "refusal", label: "Refused by the paper broker", code: "INSUFFICIENT_CASH" });
    expect(rej("CANCELLED_BY_AGENT")).toEqual({ kind: "housekeeping", label: "Withdrawn by the agent", code: null });
    expect(rej("TRIGGER_EXPIRED")).toEqual({ kind: "housekeeping", label: "Expired before it fired", code: "TRIGGER_EXPIRED" });
    expect(rej("CANCEL_TARGET_NOT_FOUND")).toEqual({ kind: "housekeeping", label: "Cancel outcome", code: "CANCEL_TARGET_NOT_FOUND" });
    expect(rej("CANCEL_NOT_OWNER")).toEqual({ kind: "housekeeping", label: "Cancel outcome", code: "CANCEL_NOT_OWNER" });
    expect(classify(order({ status: "pending", trigger: { op: ">=", level: 1 } })).kind).toBe("pending");
    expect(classify(order({ status: "pending", trigger: { op: ">=", level: 1 } })))
      .toEqual({ kind: "pending", label: "Armed with the paper broker, waiting for its trigger", code: null });
    expect(classify(order({ status: "pending" }))).toEqual({ kind: "housekeeping", label: "Not yet resolved", code: null });
  });
  it("an unknown or missing code is a refusal that does not claim the broker refused", () => {
    expect(rej("NOT_A_CODE")).toEqual({ kind: "refusal", label: "Rejected", code: "NOT_A_CODE" });
    expect(rej(null)).toEqual({ kind: "refusal", label: "Rejected", code: null });
  });
  it("every WATCHER_RAILS code is housekeeping, every other BROKER_RAILS code a refusal", () => {
    const hk = new Set(["CANCELLED_BY_AGENT", "CANCEL_TARGET_NOT_FOUND", "CANCEL_NOT_OWNER"]);
    for (const r of WATCHER_RAILS) expect(rej(r.code).kind).toBe("housekeeping");
    for (const r of BROKER_RAILS) expect(rej(r.code).kind).toBe(hk.has(r.code) ? "housekeeping" : "refusal");
  });
  // Regression: the ledger holds two trader CANCEL_TARGET_NOT_FOUND rows filed
  // under real order ids; they are cancel outcomes, not refused trades, and
  // led the hero as "Refused by the paper broker" once newest.
  it("the two committed CANCEL_TARGET_NOT_FOUND shapes never lead", () => {
    const fill = order({ order_id: "f", ts_filled: "2026-05-01T22:00:00Z" });
    const a = cancelNotFound("ord_2026-06-02_sharp-shooter-usd_003", "2026-06-02", "2026-06-03T20:12:44.609497Z");
    const b = cancelNotFound("ord_2026-05-21_sharp-shooter-usd_001", "2026-05-21", "2026-05-29T20:12:33.479257Z");
    expect(latestDecision([fill, a, b], traders)?.order_id).toBe("f");
    expect(latestDecision([a, b], traders)).toBeNull();
    expect(classify(a).kind).toBe("housekeeping");
  });
});

describe("decisionStamp", () => {
  it("a resolved record is stamped with its confirmation time", () => {
    expect(decisionStamp(order({ ts_filled: "2026-10-10T17:41:10.5Z" })))
      .toEqual({ datetime: "2026-10-10T17:41:10.5Z", text: `${longDate("2026-10-10")}, 17:41 UTC` });
  });
  // Real shape: pending/ord_2026-11-04_world_005.json, written 22:07:42.
  it("a pending record is stamped with the time it was written, not its bare day", () => {
    const pen = order({ order_id: "ord_2026-11-04_world_005", agent_id: "world", status: "pending", date: "2026-11-04",
      ticker: "DAL", shares: 11, ts_written: "2026-11-04T22:07:42.976553Z", ts_filled: null });
    expect(decisionStamp(pen)).toEqual({ datetime: "2026-11-04T22:07:42.976553Z", text: `${longDate("2026-11-04")}, 22:07 UTC` });
  });
  // Real shape: outbox/2026-05-22.jsonl holds ord_2026-05-22_sharp-shooter-eur_001
  // with ts 2026-05-23T08:29:34Z. The header prints the moment that happened:
  // the UTC day and time of ts_written, matching its own datetime. The file's
  // day is explained by the "order written" note, not spliced into the stamp.
  it("a pending record written on another UTC day than its file prints ts_written's own day", () => {
    const pen = order({ order_id: "ord_2026-05-22_sharp-shooter-eur_001", agent_id: "sharp-shooter-eur", status: "pending",
      date: "2026-05-22", ticker: "CL2.PA", ts_written: "2026-05-23T08:29:34.297684Z", ts_filled: null });
    expect(decisionStamp(pen)).toEqual({ datetime: "2026-05-23T08:29:34.297684Z", text: `${longDate("2026-05-23")}, 08:29 UTC` });
    expect(rawRecordUrl(pen)).toContain("/outbox/2026-05-22.jsonl");
  });
  it("falls back to the authored day when no timestamp exists", () => {
    expect(decisionStamp(order({ date: "2026-10-01", ts_filled: null, ts_written: null })))
      .toEqual({ datetime: "2026-10-01", text: longDate("2026-10-01") });
  });
});

describe("latestFill and latestDecision share one ordering", () => {
  it("latestFill stays fill-only while latestDecision takes any status", () => {
    const fill = order({ order_id: "f", ts_filled: "2026-10-01T22:00:00Z" });
    const rej = order({ order_id: "r", status: "rejected", ts_filled: "2026-10-09T22:00:00Z" });
    expect(latestFill([fill, rej], traders)?.order_id).toBe("f");
    expect(latestDecision([fill, rej], traders)?.order_id).toBe("r");
    expect(latestFill([rej], traders)).toBeNull();
    expect(latestDecision([rej], traders)?.order_id).toBe("r");
  });
});

describe("byFillTimeDesc", () => {
  it("puts the hero's fill first, so the tape and the hero agree", () => {
    const fired = order({ order_id: "a", date: "2026-09-16", ts_filled: "2026-10-10T17:41:10Z" });
    const recent = order({ order_id: "b", date: "2026-10-09", ts_filled: "2026-10-09T22:30:00Z" });
    const sorted = [recent, fired].sort(byFillTimeDesc);
    expect(sorted[0]).toBe(latestFill([recent, fired], traders));
  });
});

describe("raw record address", () => {
  it("every outbox order's line is its physical line in the file", () => {
    for (const o of loadAllOrders().slice(-200)) {
      const lines = fs.readFileSync(path.join(DATA_DIR, "orders", "outbox", `${o.date}.jsonl`), "utf-8").split("\n");
      expect(JSON.parse(lines[o.line! - 1]).order_id).toBe(o.order_id);
    }
  });
});

describe("formatShares", () => {
  it("drops trailing zeros", () => {
    expect(formatShares(185)).toBe("185");
    expect(formatShares(1.5)).toBe("1.5");
    expect(formatShares(0.015)).toBe("0.015");
  });
  // Regression: 8139472a2 — the banded formatShares centralised there printed
  // 150.5 as "151" and 0.00004 BTC as "0".
  it("never rounds a traded quantity to fewer digits than it has", () => {
    expect(formatShares(150.5)).toBe("150.5");
    expect(formatShares(0.00004)).toBe("0.00004");
    expect(formatShares(1234.5)).toBe("1234.5");
    expect(formatShares(1e-9)).toBe("0.000000001");
  });
  it("drops float noise from computed quantities, and prints no negative zero", () => {
    expect(formatShares(2.60575008)).toBe("2.60575");
    expect(formatShares(0.30000000000000004)).toBe("0.3");
    expect(formatShares(-0)).toBe("0");
  });
  it("never rounds an integer part longer than six digits", () => {
    expect(formatShares(1234567)).toBe("1234567");
    expect(formatShares(12345678.4)).toBe("12345678");
  });
});

describe("formatPrice", () => {
  it("groups thousands behind the ISO code", () => {
    expect(formatPrice(73348.92, "EUR")).toBe("EUR 73,348.92");
  });
  it("keeps four decimals under 10 and two above", () => {
    expect(formatPrice(1.08321, "USD")).toBe("USD 1.0832");
    expect(formatPrice(385.45001220703125, "USD")).toBe("USD 385.45");
  });
  it("never prints a sub-cent price as zero, nor 9.99996 as 10.0000", () => {
    expect(formatPrice(0.0000123, "EUR")).toBe("EUR 0.00001230");
    expect(formatPrice(9.99996, "USD")).toBe("USD 10.00");
    expect(formatPrice(0.99996, "USD")).toBe("USD 1.0000");
  });
  it("prints a dash for a missing price and omits a missing code", () => {
    expect(formatPrice(null, "EUR")).toBe("—");
    expect(formatPrice(12, null)).toBe("12.00");
  });
});

describe("pendingCondition", () => {
  // Real shape: outbox/2026-11-04.jsonl, ord_2026-11-04_world_005 — the order
  // `currency` is the book's EUR, the trigger level is in the ticker's USD.
  const day = new Date("2026-11-05T04:00:00Z"); // pinned: dal expires 2026-11-20
  const dal = order({ order_id: "ord_2026-11-04_world_005", agent_id: "world", status: "pending", date: "2026-11-04",
    ticker: "DAL", shares: 11, currency: "EUR", fill_price: null, fill_currency: null, ts_filled: null,
    ts_written: "2026-11-04T22:07:42.976553Z", trigger: { op: "<=", level: 85.5 }, expires: "2026-11-20" });
  it("states the trigger in the ticker's currency, and the expiry", () => {
    expect(pendingCondition(dal, day)).toBe("fires at \u2264 USD 85.50 \u00b7 until 20 November 2026");
  });
  it("prints a >= trigger as a symbol too", () => {
    expect(pendingCondition({ ...dal, trigger: { op: ">=", level: 120 } }, day)).toBe("fires at \u2265 USD 120.00 \u00b7 until 20 November 2026");
  });
  it("shows no condition line when the order has no trigger, expiry or not", () => {
    expect(pendingCondition({ ...dal, trigger: null }, day)).toBeNull();
    expect(pendingCondition({ ...dal, trigger: undefined }, day)).toBeNull();
  });
  it("shows the trigger alone when there is no expiry, and null when neither", () => {
    expect(pendingCondition({ ...dal, expires: null }, day)).toBe("fires at \u2264 USD 85.50");
    expect(pendingCondition({ ...dal, trigger: null, expires: undefined }, day)).toBeNull();
  });
  it("is null for a resolved record", () => {
    expect(pendingCondition({ ...dal, status: "filled" }, day)).toBeNull();
  });
  it("reads the committed ledger's armed order", () => {
    const real = loadAllOrders().find((o) => o.order_id === "ord_2026-11-04_world_005");
    expect(real?.trigger).toEqual({ op: "<=", level: 85.5 });
    expect(real?.expires).toBe("2026-11-20");
  });
});

describe("an armed order is armed only until its expiry", () => {
  // Real shape: ord_2026-11-04_world_005, expires 2026-11-20 (inclusive). The
  // 13:00 UTC sweep writes TRIGGER_EXPIRED only after the day has passed, so
  // until then the pending row would read "Armed ... until <past date>".
  const armed = order({ order_id: "ord_2026-11-04_world_005", agent_id: "world", status: "pending", date: "2026-11-04",
    ticker: "DAL", currency: "EUR", fill_price: null, fill_currency: null, ts_filled: null,
    ts_written: "2026-11-04T22:07:42Z", trigger: { op: "<=", level: 85.5 }, expires: "2026-11-20" });
  const build = (day: string) => new Date(`${day}T04:00:00Z`);
  it("classifies an order past its expiry as housekeeping with no code", () => {
    expect(classify(armed, build("2026-11-21"))).toEqual({ kind: "housekeeping", label: "Expired before it fired", code: null });
  });
  it("is still armed on the expiry day itself (expiry is inclusive)", () => {
    expect(classify(armed, build("2026-11-20")).kind).toBe("pending");
    expect(classify(armed, build("2026-11-19")).kind).toBe("pending");
  });
  it("never leads once expired, and prints no past date", () => {
    const fill = order({ order_id: "f", ts_filled: "2026-11-01T22:00:00Z" });
    expect(latestDecision([fill, armed], traders, build("2026-11-21"))?.order_id).toBe("f");
    expect(latestDecision([fill, armed], traders, build("2026-11-20"))?.order_id).toBe("ord_2026-11-04_world_005");
    expect(pendingCondition(armed, build("2026-11-21"))).toBeNull();
    expect(pendingCondition(armed, build("2026-11-20"))).toBe("fires at \u2264 USD 85.50 \u00b7 until 20 November 2026");
    expect(statusLine(armed, build("2026-11-21")).label).toBe("Expired before it fired");
  });
  it("an order with no expiry never expires", () => {
    expect(classify({ ...armed, expires: null }, build("2030-01-01")).kind).toBe("pending");
  });
});

describe("a malformed timestamp does not fail the build", () => {
  // Regression: decisionStamp called new Date(iso).toISOString() on ts_written,
  // which throws RangeError on a garbled value while index.astro renders.
  const day = "2026-11-04";
  const trig = { op: "<=" as const, level: 10 };
  it("decisionStamp falls back to the authored day when ts_written does not parse", () => {
    const o = order({ status: "pending", trigger: trig, date: day, ts_filled: null, ts_written: "garbled" });
    expect(decisionStamp(o)).toEqual({ datetime: day, text: longDate(day) });
  });
  it("decisionStamp falls back when ts_written is missing", () => {
    const o = order({ status: "pending", trigger: trig, date: day, ts_filled: null });
    delete (o as Partial<Order>).ts_written;
    expect(decisionStamp(o)).toEqual({ datetime: day, text: longDate(day) });
  });
  it("decisionStamp falls back when ts_filled does not parse", () => {
    expect(decisionStamp(order({ date: day, ts_filled: "garbled" }))).toEqual({ datetime: day, text: longDate(day) });
  });
  it("an unparseable time sorts as the start of the authored day", () => {
    const bad = order({ order_id: "bad", status: "pending", trigger: trig, date: "2026-11-04", ts_filled: null, ts_written: "garbled" });
    const before = order({ order_id: "b", ts_filled: "2026-11-03T23:59:59Z" });
    const after = order({ order_id: "a", ts_filled: "2026-11-04T00:00:01Z" });
    expect(latestDecision([before, bad], traders)?.order_id).toBe("bad");
    expect(latestDecision([after, bad], traders)?.order_id).toBe("a");
    expect(byFillTimeDesc(bad, after)).toBe(1);
  });
});

