import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

// The repo root is found by walking up to the directory holding roster.yaml
// and METHODOLOGY.md, never by counting `../`. This module is read from
// site/src/lib/ by vitest but from a bundled chunk at build time, and the
// bundler decides that chunk's depth: Astro 5 wrote dist/chunks/, where three
// `../` happened to land on the root, and Astro 7 writes dist/.prerender/chunks/,
// where the same count stopped at site/ and every getStaticPaths failed.
export function findRepoRoot(start: string): string {
  let dir = start;
  for (;;) {
    if (
      fs.existsSync(path.join(dir, "roster.yaml")) &&
      fs.existsSync(path.join(dir, "METHODOLOGY.md"))
    ) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      throw new Error(`midas repo root not found above ${start}`);
    }
    dir = parent;
  }
}

export const REPO_ROOT = findRepoRoot(path.dirname(fileURLToPath(import.meta.url)));

export const DATA_DIR = path.join(REPO_ROOT, "data");
export const AGENTS_DIR = path.join(REPO_ROOT, ".claude", "agents");
export const BLOG_DIR = path.join(DATA_DIR, "blog");
export const OUTPUT_DIR = path.join(DATA_DIR, "output");
export const POSTS_DIR = path.join(DATA_DIR, "posts");
export const MEMORY_DIR = path.join(DATA_DIR, "agent_memory");
