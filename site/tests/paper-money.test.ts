import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { SRC, stylesheetText } from "./helpers/source-files";

const read = (p: string) => fs.readFileSync(path.join(SRC, p), "utf-8");

// The rendered check is the design-critique capture at 390; this source scan
// guards the two stylesheets that can reach the footer.
// - SiteFooter.astro's own styles: nothing in the footer is legitimately
//   hidden, so any hiding declaration there — on the tagline or any ancestor —
//   fails.
// - global.css: any rule whose selector names the footer or its tagline.
// Comments are stripped first, so prose that mentions the tagline is never
// read as a selector. Other pages' `.tagline` classes are scoped to those
// pages and cannot reach the footer, so they are not scanned.
const HIDES =
  /display:\s*none|visibility:\s*hidden|(?<![-\w])clip(-path)?:|(?<![-\w])opacity:\s*0[;\s}]|(?<![-\w])font-size:\s*0[;\s}]|(?<![-\w])(?:max-)?height:\s*0[;\s}]/;
const FOOTER_SELECTOR = /site-footer|footer|tagline/;

function rules(css: string): { selector: string; body: string; text: string }[] {
  // Print styles legitimately drop the chrome from paper; they never reach a screen.
  const bare = css
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/@media\s+print\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, "");
  return [...bare.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
    selector: m[1].trim(),
    body: m[2],
    text: m[0].trim(),
  }));
}

function hidingRules(): string[] {
  const own = rules(stylesheetText(path.join(SRC, "components/SiteFooter.astro")))
    .filter((r) => HIDES.test(r.body));
  const global = rules(stylesheetText(path.join(SRC, "styles/global.css")))
    .filter((r) => FOOTER_SELECTOR.test(r.selector) && HIDES.test(r.body));
  return [...own, ...global].map((r) => r.text);
}

// The front page leads with a real-looking fill, and most visitors arrive on a
// phone: paper money is stated next to the trade, and the footer says so at
// every width.
describe("the front page states paper money", () => {
  // Regression: b5cdb47d1 — the footer hid its paper-money tagline below 48rem.
  it("neither the footer's styles nor a global footer rule hide anything", () => {
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
