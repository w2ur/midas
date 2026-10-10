import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { SRC, sourceFiles } from "./helpers/source-files";
import { TRADING_AGENTS } from "@/lib/roster";
import { MANAGER } from "@/lib/manager";

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
// Derived from the roster, so a new trader is contrast-checked with no edit
// here. The Manager (allocator) is off the ranked roster but its kit colour is
// still contrast-guarded like any agent's — see lib/manager.ts.
const AGENTS = [...TRADING_AGENTS.map((a) => a.id), MANAGER.id];
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

  // Section labels sit in --ink-muted on the raised surfaces too (the
  // methodology contents, the changelog card, the archive banner).
  for (const surface of ["bg-elev", "bg-elev2"]) {
    it(`--ink-muted on --${surface} >= 4.5, both themes`, () => {
      const t = token("ink-muted");
      const g = token(surface);
      expect(ratio(t.dark, g.dark)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(t.light, g.light)).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("--accent-ink on the amber fill (--accent) >= 4.5, both themes", () => {
    const a = token("accent");
    const ink = token("accent-ink");
    expect(ratio(ink.dark, a.dark)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(ink.light, a.light)).toBeGreaterThanOrEqual(4.5);
  });
});

// --ink-faint was declared "caption/decorative only (>= 3:1)", but the
// components set it as the `color` of real text (units, currencies, the
// footer's not-financial-advice line, disabled date-nav, placeholders). A token
// check alone passed while the token was misused, so this block finds every
// place the token colours text and holds it to AA on each surface that text
// can sit on.
/** `color: var(--ink-faint…)` — the text colour property only, not border/background/stroke. */
const FAINT_AS_TEXT = /(?:^|[\s;{])color:\s*var\(--ink-faint\b/;
const faintTextUses = sourceFiles(SRC).flatMap((file) =>
  fs
    .readFileSync(file, "utf-8")
    .split("\n")
    .map((line, i) => ({ file: path.relative(SRC, file), line: i + 1, text: line }))
    .filter((l) => FAINT_AS_TEXT.test(l.text)),
);

describe("WCAG AA — --ink-faint wherever it colours text", () => {
  it("the usage scan finds the known text uses (control: an empty scan proves nothing)", () => {
    expect(faintTextUses.length).toBeGreaterThan(0);
    expect(faintTextUses.some((u) => u.file === "styles/global.css")).toBe(true);
  });

  const f = token("ink-faint");
  for (const surface of ["bg", "bg-elev", "bg-elev2"]) {
    const s = token(surface);
    it(`--ink-faint on --${surface} (dark) >= 4.5 — used as text in ${faintTextUses.length} places`, () => {
      expect(ratio(f.dark, s.dark)).toBeGreaterThanOrEqual(4.5);
    });
    it(`--ink-faint on --${surface} (light) >= 4.5 — used as text in ${faintTextUses.length} places`, () => {
      expect(ratio(f.light, s.light)).toBeGreaterThanOrEqual(4.5);
    });
  }
});

// --bg-elev is the front page's latest-fill card, which names its agent in kit
// colour; the other surfaces print kit colours on --bg.
describe("WCAG — per-agent kit colours >= 4.5:1 as text, both themes", () => {
  for (const surface of ["bg", "bg-elev"]) {
    const ground = token(surface);
    for (const id of AGENTS) {
      const c = agentColor(id);
      it(`${id} kit on --${surface} (dark) >= 4.5`, () => {
        expect(ratio(c.dark, ground.dark)).toBeGreaterThanOrEqual(4.5);
      });
      it(`${id} kit on --${surface} (light) >= 4.5`, () => {
        expect(ratio(c.light, ground.light)).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
});

// Kit colours live in two files — roster.ts / manager.ts (signatureColor, read by charts and
// crests in TypeScript) and global.css ([data-agent], read by CSS). The checks
// above read only the CSS, so the two could drift apart unnoticed.
describe("kit colours agree between roster.ts and global.css", () => {
  for (const a of [...TRADING_AGENTS, MANAGER]) {
    it(`${a.id}`, () => {
      const css = agentColor(a.id);
      expect(a.signatureColor.light.toLowerCase()).toBe(css.light.toLowerCase());
      expect(a.signatureColor.dark.toLowerCase()).toBe(css.dark.toLowerCase());
    });
  }
});
