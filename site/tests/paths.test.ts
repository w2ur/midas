import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import { REPO_ROOT, DATA_DIR, AGENTS_DIR, BLOG_DIR, OUTPUT_DIR, POSTS_DIR, MEMORY_DIR } from "@/lib/paths";

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
