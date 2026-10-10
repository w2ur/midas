import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = path.join(__dirname, "../src");
const read = (p: string) => fs.readFileSync(path.join(SRC, p), "utf-8");

// The front page leads with a real-looking fill, and most visitors arrive on a
// phone. Paper money has to be stated next to the trade and at every width.
describe("paper money is stated wherever a fill is shown", () => {
  // Regression: b5cdb47d1 — the footer hid its paper-money tagline below 48rem.
  it("the footer tagline is never hidden", () => {
    const footer = read("components/SiteFooter.astro");
    expect(footer).toContain('class="tagline"');
    expect(footer).not.toMatch(/\.tagline\s*\{[^}]*display:\s*none/);
  });

  // Regression: b5cdb47d1 — the hero card said "Filled by the broker".
  it("the hero card and the tape name the paper broker", () => {
    expect(read("components/LatestFill.astro")).toContain("Filled by the paper broker");
    expect(read("pages/index.astro")).toContain("refusable by the paper broker");
  });
});
