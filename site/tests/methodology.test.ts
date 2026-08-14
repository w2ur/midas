import { describe, it, expect } from "vitest";
import {
  methodologyDoc,
  methodologyToc,
  renderMethodology,
  renderChangelog,
  rewriteAnchorLinks,
  addHeadingIds,
  slugify,
  CHANGELOG_PATH,
  METHODOLOGY_PATH,
} from "../src/lib/methodology";

describe("methodology document split", () => {
  it("puts the changelog on its own side of the split", () => {
    const doc = methodologyDoc();
    expect(doc.changelog).toContain("## Methodology changelog");
    expect(doc.beforeStatus + doc.afterStatus).not.toContain("## Methodology changelog");
  });

  it("keeps the colophon on the methodology page, not the changelog", () => {
    // The changelog runs *to* the colophon, not through it. A slice that ran to
    // end-of-file would silently swallow the colophon onto the wrong page.
    const doc = methodologyDoc();
    expect(doc.afterStatus).toContain("## Colophon");
    expect(doc.changelog).not.toContain("## Colophon");
  });

  it("moves every changelog anchor and keeps the body anchor", () => {
    const doc = methodologyDoc();
    // These fifteen ids are cited from shipped commit messages, from
    // src/lib/ledger-notes.ts, and from each other. Losing one is a broken
    // disclosure link, so the list is asserted explicitly rather than by count.
    expect(doc.changelogAnchors).toEqual([
      "gate-c-evaluation-2026-08-14",
      "leaderboard-rerank-2026-08-14",
      "complete-bars-2026-08-12",
      "sentiment-collection-hour-2026-08-12",
      "close-basis-2026-08-07",
      "oracle-fabrication-2026-08-07",
      "sentiment-lag-2026-08-07",
      "published-data-gates-2026-08-07",
      "rails-calibration-2026-08-07",
      "store-units-2026-08-07",
      "sweep-and-restatement-2026-08-07",
      "no-session-2026-08-06",
      "stale-prices-2026-08-06",
      "snapshot-overwrite-2026-08-03",
      "lost-fill-2026-05-21",
    ]);
    expect(doc.bodyAnchors).toContain("open-source");
    // Cited from the 2026-08-14 changelog entry; the changelog page repoints
    // only ids present in bodyAnchors, so these must stay explicit <a id>s.
    expect(doc.bodyAnchors).toContain("known-distortions");
    expect(doc.bodyAnchors).toContain("the-noise-statement");
  });

  it("renders every moved anchor into the changelog page and none into the essay", () => {
    const essay = renderMethodology();
    const changelog = renderChangelog();
    for (const id of essay.movedAnchors) {
      expect(changelog.html).toContain(`id="${id}"`);
      expect(essay.htmlBefore + essay.htmlAfter).not.toContain(`id="${id}"`);
    }
    expect(changelog.entryCount).toBe(15);
  });

  it("does not print the changelog heading twice on the changelog page", () => {
    expect(renderChangelog().html).not.toContain("Methodology changelog</h");
  });
});

describe("anchor link rewriting", () => {
  const opts = {
    selfAnchors: ["open-source"],
    otherAnchors: ["lost-fill-2026-05-21"],
    otherPath: CHANGELOG_PATH,
  };

  it("repoints a link whose target moved", () => {
    expect(rewriteAnchorLinks('<a href="#lost-fill-2026-05-21">x</a>', opts)).toBe(
      `<a href="${CHANGELOG_PATH}#lost-fill-2026-05-21">x</a>`,
    );
  });

  it("leaves a same-page link alone", () => {
    const html = '<a href="#open-source">x</a>';
    expect(rewriteAnchorLinks(html, opts)).toBe(html);
  });

  it("leaves an unknown fragment alone rather than guessing a page for it", () => {
    const html = '<a href="#not-an-anchor">x</a>';
    expect(rewriteAnchorLinks(html, opts)).toBe(html);
  });

  it("rewrites the real essay links that point into the changelog", () => {
    // The essay body cites four changelog entries. Rendered on the split
    // pages these must be absolute, or they scroll to nothing.
    const { htmlBefore, htmlAfter } = renderMethodology();
    const html = htmlBefore + htmlAfter;
    expect(html).toContain(`${CHANGELOG_PATH}#lost-fill-2026-05-21`);
    expect(html).toContain(`${CHANGELOG_PATH}#sweep-and-restatement-2026-08-07`);
    expect(html).not.toMatch(/href="#(lost-fill|sweep-and-restatement)/);
  });

  it("repoints changelog links that point back at the essay", () => {
    const html = renderChangelog().html;
    expect(html).not.toMatch(/href="#open-source"/);
    // Cross-references *within* the changelog stay same-page.
    expect(html).toContain('href="#sweep-and-restatement-2026-08-07"');
    expect(html).not.toContain(`${METHODOLOGY_PATH}#sweep-and-restatement-2026-08-07`);
  });
});

describe("table of contents", () => {
  it("lists the essay's sections and not the changelog", () => {
    const labels = methodologyToc().map((t) => t.label);
    expect(labels).toContain("What Midas is");
    expect(labels).toContain("Colophon");
    expect(labels).not.toContain("Methodology changelog");
  });

  it("emits an id for every heading it links to", () => {
    // marked 14 renders a bare <h2> with no id at all, so the contents would
    // link to fragments that do not exist unless addHeadingIds mints them.
    // Confirmed capable of failing: drop addHeadingIds from renderMethodology
    // and this goes red.
    const { htmlBefore, htmlAfter } = renderMethodology();
    const html = htmlBefore + htmlAfter;
    for (const item of methodologyToc()) {
      expect(html).toContain(`<h2 id="${item.id}"`);
    }
  });

  it("derives ids that survive punctuation in the heading", () => {
    expect(slugify("The controls (this is the part most projects skip)")).toBe(
      "the-controls-this-is-the-part-most-projects-skip",
    );
    expect(slugify("Pre-registered experiment: sentiment A/B")).toBe(
      "pre-registered-experiment-sentiment-ab",
    );
  });

  it("ids a heading that contains inline markup", () => {
    expect(addHeadingIds("<h2>What <em>is</em> measured</h2>")).toBe(
      '<h2 id="what-is-measured">What <em>is</em> measured</h2>',
    );
  });
});
