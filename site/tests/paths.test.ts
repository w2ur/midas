import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { REPO_ROOT, DATA_DIR, AGENTS_DIR, BLOG_DIR, OUTPUT_DIR, POSTS_DIR, MEMORY_DIR, findRepoRoot } from "@/lib/paths";

describe("paths", () => {
  it("REPO_ROOT points at the midas repo root (contains CLAUDE.md and data/)", () => {
    expect(fs.existsSync(`${REPO_ROOT}/CLAUDE.md`)).toBe(true);
    expect(fs.existsSync(`${REPO_ROOT}/data`)).toBe(true);
  });

  it("DATA_DIR exists", () => {
    expect(fs.existsSync(DATA_DIR)).toBe(true);
  });

  it("AGENTS_DIR contains at least 10 persona files", () => {
    expect(fs.existsSync(AGENTS_DIR)).toBe(true);
    const files = fs.readdirSync(AGENTS_DIR).filter((f) => f.endsWith(".md"));
    expect(files.length).toBeGreaterThanOrEqual(10);
  });

  it("BLOG_DIR, OUTPUT_DIR, POSTS_DIR, MEMORY_DIR all exist", () => {
    expect(fs.existsSync(BLOG_DIR)).toBe(true);
    expect(fs.existsSync(OUTPUT_DIR)).toBe(true);
    expect(fs.existsSync(POSTS_DIR)).toBe(true);
    expect(fs.existsSync(MEMORY_DIR)).toBe(true);
  });
});

describe("findRepoRoot", () => {
  // Regression: the Astro 7 upgrade — REPO_ROOT counted three `../` from the
  // bundled chunk, which Astro 7 moved one level deeper (dist/.prerender/chunks),
  // so the build read site/data/output and failed.
  it("finds the root from any depth below it", () => {
    for (const depth of ["site/src/lib", "site/dist/chunks", "site/dist/.prerender/chunks"]) {
      expect(findRepoRoot(path.join(REPO_ROOT, depth))).toBe(REPO_ROOT);
    }
  });

  it("throws outside a midas checkout instead of guessing", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "midas-paths-"));
    expect(() => findRepoRoot(tmp)).toThrow(/repo root not found/);
  });
});
