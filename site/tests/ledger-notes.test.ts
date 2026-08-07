import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { getLedgerNote, LEDGER_NOTES } from "../src/lib/ledger-notes";
import { TRADING_AGENTS } from "../src/lib/roster";
import { REPO_ROOT } from "../src/lib/paths";

describe("ledger-notes", () => {
  it("carries the sharp-shooter-eur incident with the documented facts", () => {
    const note = getLedgerNote("sharp-shooter-eur");
    expect(note).not.toBeNull();
    expect(note!.orderId).toBe("ord_2026-05-21_sharp-shooter-eur_001");
    expect(note!.summary.length).toBeGreaterThan(0);
    expect(note!.summary).toMatch(/reconciled/i);
    expect(note!.methodologyHref).toMatch(/^\/methodology#/);
  });

  it("carries the three quote-currency reconciliations of 2026-08-07", () => {
    // world (LSE pence read as pounds), goldfinger (.L quoting in USD) and
    // monsieur-forex (pairs quoting in their second leg) each had fills
    // converted at a guessed currency. Only world's is a single order, so
    // only world's pins an orderId.
    for (const id of ["world", "goldfinger", "monsieur-forex"] as const) {
      const note = getLedgerNote(id);
      expect(note, `${id} should carry a ledger note`).not.toBeNull();
      expect(note!.summary).toMatch(/reconciled/i);
      expect(note!.methodologyHref).toBe("/methodology#sweep-and-restatement-2026-08-07");
    }
    expect(getLedgerNote("world")!.orderId).toBe("ord_2026-08-05_world_001");
    expect(getLedgerNote("goldfinger")!.orderId).toBeUndefined();
  });

  it("returns null for agents with no known ledger artifact", () => {
    // Derived from LEDGER_NOTES rather than hardcoded, so adding an incident
    // does not falsify this test — but every agent NOT listed must still
    // return null, which is the property worth guarding.
    const clean = TRADING_AGENTS.filter((a) => !(a.id in LEDGER_NOTES));
    expect(clean.length).toBeGreaterThan(0);
    for (const agent of clean) {
      expect(getLedgerNote(agent.id), `${agent.id} should have no note`).toBeNull();
    }
  });

  it("returns null for an unknown agent id", () => {
    expect(getLedgerNote("not-a-real-agent")).toBeNull();
  });

  it("every note's methodologyHref anchor actually exists in METHODOLOGY.md", () => {
    const md = fs.readFileSync(path.join(REPO_ROOT, "METHODOLOGY.md"), "utf-8");
    for (const note of Object.values(LEDGER_NOTES)) {
      const anchorId = note!.methodologyHref.split("#")[1];
      expect(md).toContain(`id="${anchorId}"`);
    }
  });

  it("does not duplicate the current reported return in the note", () => {
    // The note deliberately does not hardcode the live return figure, so it
    // can never drift out of sync with the number rendered next to it.
    const note = getLedgerNote("sharp-shooter-eur")!;
    expect(note).not.toHaveProperty("reportedReturnPct");
  });
});
