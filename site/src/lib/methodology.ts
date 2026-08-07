/**
 * METHODOLOGY.md → the /methodology and /methodology/changelog pages.
 *
 * ── Why the document is split for rendering, but not on disk ──
 * `METHODOLOGY.md` is a single file and stays one: `engine/disclosure.py`
 * refuses a restatement whose `--changelog-entry` anchor does not resolve to a
 * real `<a id>` in it, so splitting the file would break the precondition that
 * makes a restatement disclose itself. Only the *rendering* is split.
 *
 * The reason to split it: the changelog is 9,571 of the document's 11,928 words
 * — 80% — and serves a different reader (an auditor checking what moved) than
 * the rest (a newcomer asking what this is). Rendered as one page it measured
 * 37 screens, and the ~2,350-word methodology proper was buried under a
 * restatement log nobody arrives wanting.
 *
 * ── Anchors are the load-bearing part ──
 * Eleven `<a id>` anchors live inside the changelog and are cited from shipped
 * commit messages, from `data/**` ledger notes, and from other changelog
 * entries. Moving them to a second URL must not break a single one, so:
 *   - in-document links are rewritten to point at whichever page now owns the
 *     target anchor (both directions — see `rewriteAnchorLinks`);
 *   - `/methodology` keeps a client-side hash forwarder for the eleven moved
 *     anchors, because a fragment is never sent to the server and no redirect
 *     rule can see it.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { marked } from "marked";
import { REPO_ROOT } from "./paths";

export const METHODOLOGY_PATH = "/methodology";
export const CHANGELOG_PATH = "/methodology/changelog";

const CHANGELOG_HEADING = "## Methodology changelog";
const COLOPHON_HEADING = "## Colophon";
/** Splits the essay so the generated pre-registration block can be injected. */
const STATUS_HEADING = "## Known distortions in the current leaderboard";

const ANCHOR_RE = /<a id="([^"]+)"><\/a>/g;

function rawMarkdown(): string {
  const mdPath = path.join(REPO_ROOT, "METHODOLOGY.md");
  return fs.existsSync(mdPath) ? fs.readFileSync(mdPath, "utf-8") : "";
}

function anchorsIn(md: string): string[] {
  return Array.from(md.matchAll(ANCHOR_RE), (m) => m[1]);
}

export interface MethodologyDoc {
  /** Essay up to the known-distortions heading. */
  beforeStatus: string;
  /** Known-distortions onward, with the changelog section removed. */
  afterStatus: string;
  /** The changelog section alone, heading included. */
  changelog: string;
  /** Anchor ids that now live on the changelog page. */
  changelogAnchors: string[];
  /** Anchor ids that stay on the methodology page. */
  bodyAnchors: string[];
}

/**
 * Slice the document into its rendered parts.
 *
 * The changelog runs from its own heading to `## Colophon` (the last section),
 * so the colophon stays on the methodology page where it belongs. If either
 * heading is ever renamed the split degrades to "everything on one page"
 * rather than silently dropping a section — a long page is a worse page, but
 * losing the changelog would be losing the disclosure record.
 */
export function methodologyDoc(): MethodologyDoc {
  const raw = rawMarkdown();

  const clStart = raw.indexOf(CHANGELOG_HEADING);
  const clEnd = raw.indexOf(COLOPHON_HEADING);
  const splittable = clStart !== -1 && clEnd !== -1 && clEnd > clStart;

  const changelog = splittable ? raw.slice(clStart, clEnd) : "";
  const body = splittable ? raw.slice(0, clStart) + raw.slice(clEnd) : raw;

  const statusIdx = body.indexOf(STATUS_HEADING);
  const beforeStatus = statusIdx === -1 ? body : body.slice(0, statusIdx);
  const afterStatus = statusIdx === -1 ? "" : body.slice(statusIdx);

  return {
    beforeStatus,
    afterStatus,
    changelog,
    changelogAnchors: anchorsIn(changelog),
    bodyAnchors: anchorsIn(body),
  };
}

/**
 * Repoint same-document `#anchor` links at the page that now owns the target.
 *
 * Runs over the rendered HTML rather than the markdown so it catches links
 * `marked` produced from reference-style syntax too. A link to an anchor on the
 * *current* page is left alone, so in-page navigation stays instant and the
 * changelog's dense internal cross-references keep working.
 */
export function rewriteAnchorLinks(
  html: string,
  opts: { selfAnchors: string[]; otherAnchors: string[]; otherPath: string },
): string {
  const { selfAnchors, otherAnchors, otherPath } = opts;
  const self = new Set(selfAnchors);
  return html.replace(/href="#([^"]+)"/g, (whole, id: string) => {
    if (self.has(id)) return whole;
    return otherAnchors.includes(id) ? `href="${otherPath}#${id}"` : whole;
  });
}

export interface RenderedMethodology {
  htmlBefore: string;
  htmlAfter: string;
  /** Anchors that moved to the changelog page — fed to the hash forwarder. */
  movedAnchors: string[];
}

/**
 * Give every `<h2>` an id derived from its own text.
 *
 * `marked` has emitted heading ids in no version this project has used (14.x
 * renders a bare `<h2>`), so the sticky contents would link to fragments that
 * do not exist. Ids are minted here with the same `slugify` the table of
 * contents calls, which makes the two agree by construction rather than by
 * a matching pair of regexes that can drift apart.
 */
export function addHeadingIds(html: string): string {
  return html.replace(/<h2>([\s\S]*?)<\/h2>/g, (_whole, inner: string) => {
    const text = inner.replace(/<[^>]+>/g, "");
    return `<h2 id="${slugify(text)}">${inner}</h2>`;
  });
}

/** The /methodology page: the essay, with changelog links repointed. */
export function renderMethodology(): RenderedMethodology {
  const doc = methodologyDoc();
  const opts = {
    selfAnchors: doc.bodyAnchors,
    otherAnchors: doc.changelogAnchors,
    otherPath: CHANGELOG_PATH,
  };
  const render = (md: string) =>
    addHeadingIds(rewriteAnchorLinks(marked.parse(md, { async: false }) as string, opts));
  return {
    htmlBefore: render(doc.beforeStatus),
    htmlAfter: render(doc.afterStatus),
    movedAnchors: doc.changelogAnchors,
  };
}

export interface RenderedChangelog {
  html: string;
  /** One entry per disclosed change — drives the count shown on both pages. */
  entryCount: number;
}

/** The /methodology/changelog page: the log alone, with body links repointed. */
export function renderChangelog(): RenderedChangelog {
  const doc = methodologyDoc();
  // Strip the section's own `##` heading — the page supplies its own <h1>, and
  // leaving it would print the title twice.
  const withoutHeading = doc.changelog.replace(CHANGELOG_HEADING, "").trimStart();
  const html = rewriteAnchorLinks(marked.parse(withoutHeading, { async: false }) as string, {
    selfAnchors: doc.changelogAnchors,
    otherAnchors: doc.bodyAnchors,
    otherPath: METHODOLOGY_PATH,
  });
  return { html, entryCount: doc.changelogAnchors.length };
}

export interface TocItem {
  id: string;
  label: string;
}

/**
 * Section list for the methodology page's sticky contents — one item per `##`
 * heading that survives the split, in document order.
 */
export function methodologyToc(): TocItem[] {
  const doc = methodologyDoc();
  const md = doc.beforeStatus + doc.afterStatus;
  return Array.from(md.matchAll(/^## (.+)$/gm), (m) => {
    const label = m[1].trim();
    return { id: slugify(label), label };
  });
}

/** Heading text → fragment id. The single definition; `addHeadingIds` uses it too. */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}
