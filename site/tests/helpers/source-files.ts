import * as fs from "node:fs";
import * as path from "node:path";

/** The site's source tree, as the source-scanning guards read it. */
export const SRC = path.join(__dirname, "../../src");

/** Every .astro and .css file under `dir` — the files that can carry styles. */
export function sourceFiles(dir: string = SRC): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return sourceFiles(p);
    return /\.(astro|css)$/.test(e.name) ? [p] : [];
  });
}

/** The CSS a file carries: a .css file whole, an .astro file's <style> blocks only. */
export function stylesheetText(file: string): string {
  const text = fs.readFileSync(file, "utf-8");
  if (file.endsWith(".css")) return text;
  return [...text.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join("\n");
}
