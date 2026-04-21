import * as path from "node:path";
import { fileURLToPath } from "node:url";

// This file lives at site/src/lib/paths.ts. Repo root is three ../ up.
const here = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(here, "..", "..", "..");

export const DATA_DIR = path.join(REPO_ROOT, "data");
export const AGENTS_DIR = path.join(REPO_ROOT, ".claude", "agents");
export const BLOG_DIR = path.join(DATA_DIR, "blog");
export const OUTPUT_DIR = path.join(DATA_DIR, "output");
export const POSTS_DIR = path.join(DATA_DIR, "posts");
export const MEMORY_DIR = path.join(DATA_DIR, "agent_memory");
