import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import common from "@/data/privacy/privacy.common.json";
import hosts from "@/data/privacy/privacy.hosts.json";
import site from "@/data/privacy/privacy.midas.json";
import {
  renderParagraph, renderNotice, mergeLinks, findSection, midasNotice, resolveLink,
  type Block,
} from "@/lib/privacy";

const dir = fileURLToPath(new URL("../src/data/privacy/", import.meta.url));
const files = { common, hosts, site } as unknown as Record<string, Block>;
const blocks = { site: site as unknown as Block, common: common as unknown as Block, hosts: hosts as unknown as Block };

describe("renderParagraph", () => {
  const links = { a: "https://example.com/x", m: "mailto:p@example.com" };
  it("resolves https links with rel=noopener and mailto without", () => {
    expect(renderParagraph("see [here](a)", links)).toBe('see <a href="https://example.com/x" rel="noopener">here</a>');
    expect(renderParagraph("[mail](m)", links)).toBe('<a href="mailto:p@example.com">mail</a>');
  });
  it("escapes everything else", () => {
    expect(renderParagraph("<b>&\"'", links)).toBe("&lt;b&gt;&amp;&quot;&#39;");
    expect(renderParagraph("[<i>](a)", links)).toContain("&lt;i&gt;");
  });
  it("fails loudly on an unknown link key", () => {
    expect(() => renderParagraph("[x](nope)", links)).toThrow(/unknown link key "nope"/);
  });
  it("refuses non-https/mailto targets", () => {
    expect(() => resolveLink("k", { k: "javascript:alert(1)" })).toThrow();
    expect(() => resolveLink("k", { k: "http://example.com" })).toThrow();
  });
});

describe("mergeLinks", () => {
  it("site wins over common over hosts", () => {
    expect(mergeLinks({ a: "S" }, { a: "C", b: "C" }, { a: "H", b: "H", c: "H" })).toEqual({ a: "S", b: "C", c: "H" });
  });
});

describe("vendored files", () => {
  it("every section has FR/EN paragraph parity", () => {
    for (const [name, b] of Object.entries(files)) {
      for (const s of b.sections) {
        expect(s.paragraphsFr.length, `${name}:${s.id}`).toBe(s.paragraphs.length);
        expect(s.paragraphs.length).toBeGreaterThan(0);
      }
    }
  });
  it("every compose entry resolves", () => {
    for (const ref of (site as { compose: string[] }).compose) expect(() => findSection(blocks, ref)).not.toThrow();
  });
  it("an unresolvable compose entry throws", () => {
    expect(() => findSection(blocks, "site:missing")).toThrow(/does not resolve/);
  });
  it("no VÉRIFIER in any vendored file", () => {
    for (const f of readdirSync(dir)) expect(readFileSync(join(dir, f), "utf8"), f).not.toMatch(/V[ÉE]RIFIER/i);
  });
  it("every link key in the text resolves", () => {
    for (const lang of ["en", "fr"] as const) expect(() => midasNotice(lang)).not.toThrow();
  });
});

describe("renderNotice", () => {
  const mk = (status?: string): Block & { compose: string[] } => ({
    block: "site", links: {}, compose: ["site:a", "site:b"],
    sections: [
      { id: "a", title: "A", titleFr: "Af", paragraphs: ["p"], paragraphsFr: ["pf"] },
      { id: "b", title: "B", titleFr: "Bf", paragraphs: ["q"], paragraphsFr: ["qf"], status },
    ],
  });
  const empty = { block: "x", sections: [] } as Block;
  it("never renders a pending section", () => {
    expect(renderNotice(mk("pending"), { common: empty, hosts: empty }, "en").map((s) => s.id)).toEqual(["a"]);
    expect(renderNotice(mk(), { common: empty, hosts: empty }, "en").map((s) => s.id)).toEqual(["a", "b"]);
  });
  it("picks FR or EN text", () => {
    expect(renderNotice(mk(), { common: empty, hosts: empty }, "fr")[0]).toMatchObject({ title: "Af", paragraphs: ["pf"] });
  });
  it("renders the midas notice in compose order", () => {
    const ids = midasNotice("en").map((s) => s.id);
    expect(ids).toEqual(["what-this-covers", "editor", "host-vercel", "nothing-asked", "analytics", "storage", "rights", "contact", "changes"]);
  });
});
