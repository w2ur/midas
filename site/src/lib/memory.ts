import * as fs from "node:fs";
import * as path from "node:path";
import { marked } from "marked";
import { MEMORY_DIR } from "./paths";
import type { AgentId } from "./roster";

function memoryFile(id: AgentId): string {
  return path.join(MEMORY_DIR, `${id}.md`);
}

export function hasMemory(id: AgentId): boolean {
  return fs.existsSync(memoryFile(id));
}

export function loadMemory(id: AgentId): string {
  const file = memoryFile(id);
  if (!fs.existsSync(file)) {
    throw new Error(`Memory file not found: ${file}`);
  }
  return fs.readFileSync(file, "utf-8");
}

export function renderMemoryHtml(markdown: string): string {
  return marked.parse(markdown, { async: false }) as string;
}
