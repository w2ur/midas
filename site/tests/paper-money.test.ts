import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = path.join(__dirname, "../src");
const read = (p: string) => fs.readFileSync(path.join(SRC, p), "utf-8");

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return sourceFiles(p);
    return /\.(astro|css)$/.test(e.name) ? [p] : [];
  });
}

// Every CSS rule in src/ whose selector could reach the footer tagline.
function rulesReachingTagline(): { file: string; rule: string }[] {
  const out: { file: string; rule: string }[] = [];
  for (const file of sourceFiles(SRC)) {
    for (const m of fs.readFileSync(file, "utf-8").matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (/tagline|site-footer\s+p|\.inner\s+p/.test(m[1])) out.push({ file, rule: m[0] });
    }
  }
  return out;
}

// The front page leads with a real-looking fill, and most visitors arrive on a
// phone. Paper money has to be stated next to the trade and at every width.
// A source scan cannot prove what a browser draws: the design-critique capture
// at 390 is the rendered check; this guards the ways it was broken before.
describe("paper money is stated wherever a fill is shown", () => {
  // Regression: b5cdb47d1 — the footer hid its paper-money tagline below 48rem.
  it("no rule anywhere in src/ hides the footer tagline", () => {
    const footer = read("components/SiteFooter.astro");
    expect(footer).toMatch(/<p class="tagline">/);
    expect(footer).not.toMatch(/<p class="tagline"[^>]*\bhidden\b/);
    const hiding = rulesReachingTagline().filter(({ rule }) =>
      /display:\s*none|visibility:\s*hidden|clip(-path)?:|opacity:\s*0[;\s}]/.test(rule),
    );
    expect(hiding).toEqual([]);
  });

  // Regression: b5cdb47d1 — the hero card said "Filled by the broker".
  it("the promise, the hero card and the tape name the paper broker", () => {
    const index = read("pages/index.astro");
    expect(index).toContain("every one the paper broker refuses");
    expect(index).toContain("refusable by the paper broker");
    expect(read("components/LatestFill.astro")).toContain("Filled by the paper broker");
  });
});
