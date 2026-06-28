import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

// Guards the "After Hours" palette against WCAG AA regressions in BOTH themes.
// Parses global.css directly, so any future token edit that breaks contrast
// fails here — the test is the source of truth, kept in sync automatically.

const CSS = fs.readFileSync(
  path.join(__dirname, "../src/styles/global.css"),
  "utf-8",
);

function srgbToLin(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function luminance(hex: string): number {
  const m = hex.replace("#", "");
  const r = parseInt(m.slice(0, 2), 16);
  const g = parseInt(m.slice(2, 4), 16);
  const b = parseInt(m.slice(4, 6), 16);
  return 0.2126 * srgbToLin(r) + 0.7152 * srgbToLin(g) + 0.0722 * srgbToLin(b);
}
function ratio(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** Extract `--name: light-dark(#light, #dark)` → { light, dark }. */
function token(name: string): { light: string; dark: string } {
  const re = new RegExp(
    `--${name}:\\s*light-dark\\(\\s*(#[0-9a-fA-F]{6})\\s*,\\s*(#[0-9a-fA-F]{6})\\s*\\)`,
  );
  const m = CSS.match(re);
  if (!m) throw new Error(`token --${name} (light-dark) not found in global.css`);
  return { light: m[1], dark: m[2] };
}

const bg = token("bg");
const AGENTS = [
  "steady-eddie-eur", "steady-eddie-usd", "sharp-shooter-eur", "sharp-shooter-usd",
  "yolo-sapiens-eur", "yolo-sapiens-usd", "satoshi", "monsieur-forex", "goldfinger", "world",
];
function agentColor(id: string): { light: string; dark: string } {
  const re = new RegExp(
    `\\[data-agent="${id}"\\][^{]*\\{[^}]*--agent-color:\\s*light-dark\\(\\s*(#[0-9a-fA-F]{6})\\s*,\\s*(#[0-9a-fA-F]{6})\\s*\\)`,
  );
  const m = CSS.match(re);
  if (!m) throw new Error(`kit colour for ${id} not found`);
  return { light: m[1], dark: m[2] };
}

describe("WCAG AA contrast — body/important text >= 4.5:1, both themes", () => {
  for (const name of ["ink", "ink-muted", "accent", "pos", "neg", "ref"]) {
    const t = token(name);
    it(`--${name} on --bg (dark) >= 4.5`, () => {
      expect(ratio(t.dark, bg.dark)).toBeGreaterThanOrEqual(4.5);
    });
    it(`--${name} on --bg (light) >= 4.5`, () => {
      expect(ratio(t.light, bg.light)).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("--accent-ink on the amber fill (--accent) >= 4.5, both themes", () => {
    const a = token("accent");
    const ink = token("accent-ink");
    expect(ratio(ink.dark, a.dark)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(ink.light, a.light)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("WCAG — caption tokens >= 3:1 (large/incidental only)", () => {
  const f = token("ink-faint");
  it("--ink-faint on --bg (dark) >= 3", () => {
    expect(ratio(f.dark, bg.dark)).toBeGreaterThanOrEqual(3);
  });
  it("--ink-faint on --bg (light) >= 3", () => {
    expect(ratio(f.light, bg.light)).toBeGreaterThanOrEqual(3);
  });
});

describe("WCAG — per-agent kit colours >= 4.5:1 as text, both themes", () => {
  for (const id of AGENTS) {
    const c = agentColor(id);
    it(`${id} kit on --bg (dark) >= 4.5`, () => {
      expect(ratio(c.dark, bg.dark)).toBeGreaterThanOrEqual(4.5);
    });
    it(`${id} kit on --bg (light) >= 4.5`, () => {
      expect(ratio(c.light, bg.light)).toBeGreaterThanOrEqual(4.5);
    });
  }
});
