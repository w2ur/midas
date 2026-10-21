// Privacy notice renderer. The text lives in vendored JSON (src/data/privacy/,
// byte-identical copies of the shared files); this module only composes it.
import common from "@/data/privacy/privacy.common.json";
import hosts from "@/data/privacy/privacy.hosts.json";
import site from "@/data/privacy/privacy.midas.json";

export type Lang = "en" | "fr";

export interface Section {
  id: string;
  title: string;
  titleFr: string;
  paragraphs: string[];
  paragraphsFr: string[];
  status?: string;
}
export interface Block {
  block: string;
  links?: Record<string, string>;
  sections: Section[];
  compose?: string[];
}
export interface RenderedSection {
  id: string;
  title: string;
  paragraphs: string[]; // HTML, already escaped
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function resolveLink(key: string, links: Record<string, string>): string {
  const url = links[key];
  if (url === undefined) throw new Error(`privacy: unknown link key "${key}"`);
  if (!/^(https:\/\/|mailto:)/.test(url)) {
    throw new Error(`privacy: link "${key}" must be https: or mailto:, got "${url}"`);
  }
  return url;
}

/** Merge link maps; earlier maps win (site, then common, then hosts). */
export function mergeLinks(...maps: Array<Record<string, string> | undefined>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of [...maps].reverse()) Object.assign(out, m ?? {});
  return out;
}

/** Escape a paragraph and turn [text](key) into anchors. Throws on an unknown key. */
export function renderParagraph(text: string, links: Record<string, string>): string {
  const re = /\[([^\]]+)\]\(([^)]+)\)/g;
  let out = "";
  let last = 0;
  for (const m of text.matchAll(re)) {
    out += escapeHtml(text.slice(last, m.index));
    const url = resolveLink(m[2], links);
    const rel = url.startsWith("https:") ? ' rel="noopener"' : "";
    out += `<a href="${escapeHtml(url)}"${rel}>${escapeHtml(m[1])}</a>`;
    last = m.index! + m[0].length;
  }
  return out + escapeHtml(text.slice(last));
}

export function findSection(blocks: Record<string, Block>, ref: string): Section {
  const [bk, id] = ref.split(":");
  const block = blocks[bk];
  const section = block?.sections.find((s) => s.id === id);
  if (!section) throw new Error(`privacy: compose entry "${ref}" does not resolve`);
  return section;
}

export function renderNotice(
  siteFile: Block & { compose: string[] },
  blocks: { common: Block; hosts: Block },
  lang: Lang,
): RenderedSection[] {
  const all: Record<string, Block> = { site: siteFile, common: blocks.common, hosts: blocks.hosts };
  const links = mergeLinks(siteFile.links, blocks.common.links, blocks.hosts.links);
  const out: RenderedSection[] = [];
  for (const ref of siteFile.compose) {
    const s = findSection(all, ref);
    if (s.status === "pending") continue;
    const paras = lang === "fr" ? s.paragraphsFr : s.paragraphs;
    out.push({
      id: s.id,
      title: lang === "fr" ? s.titleFr : s.title,
      paragraphs: paras.map((p) => renderParagraph(p, links)),
    });
  }
  return out;
}

export const UPDATED = { en: "Last updated 21 October 2026", fr: "Mis à jour le 21 octobre 2026" } as const;

export function midasNotice(lang: Lang = "en"): RenderedSection[] {
  return renderNotice(site as unknown as Block & { compose: string[] }, {
    common: common as unknown as Block,
    hosts: hosts as unknown as Block,
  }, lang);
}
