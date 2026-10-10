import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { SRC, sourceFiles, stylesheetText } from "./helpers/source-files";

const read = (p: string) => fs.readFileSync(path.join(SRC, p), "utf-8");

// Rules whose selector names the tagline or the footer's paragraphs and that
// hide what they match. A source scan cannot prove what a browser draws — a
// selector that reaches the line some other way escapes it — so the rendered
// check is the design-critique capture at 390. This guards the shapes the
// line has been, or could plausibly be, hidden by.
const FOOTER_TEXT_SELECTOR = /tagline|site-footer[^,{]*\bp\b|\.inner[^,{]*\bp\b|footer[^,{]*\bp\b/;
const HIDES =
  /display:\s*none|visibility:\s*hidden|clip(-path)?:|opacity:\s*0[;\s}]|font-size:\s*0[;\s}]|(?:max-)?height:\s*0[;\s}]/;

function hidingRules(): { file: string; rule: string }[] {
  return sourceFiles().flatMap((file) =>
    [...stylesheetText(file).matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .filter((m) => FOOTER_TEXT_SELECTOR.test(m[1]) && HIDES.test(m[2]))
      .map((m) => ({ file: path.relative(SRC, file), rule: m[0].trim() })),
  );
}

// The front page leads with a real-looking fill, and most visitors arrive on a
// phone: paper money is stated next to the trade, and the footer says so at
// every width.
describe("the front page states paper money", () => {
  // Regression: b5cdb47d1 — the footer hid its paper-money tagline below 48rem.
  it("no stylesheet rule hides the footer tagline or the footer's paragraphs", () => {
    const footer = read("components/SiteFooter.astro");
    expect(footer).toMatch(/<p class="tagline">/);
    expect(footer).not.toMatch(/<p class="tagline"[^>]*\bhidden\b/);
    expect(hidingRules()).toEqual([]);
  });

  // Regression: b5cdb47d1 — the hero card said "Filled by the broker".
  it("the promise, the hero card and the tape name the paper broker", () => {
    const index = read("pages/index.astro");
    expect(index).toContain("every one the paper broker refuses");
    expect(index).toContain("refusable by the paper broker");
    expect(read("components/LatestFill.astro")).toContain("Filled by the paper broker");
  });
});
