import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

type HeaderRule = { source: string; headers: { key: string; value: string }[] };

const config = JSON.parse(
  readFileSync(fileURLToPath(new URL("../vercel.json", import.meta.url)), "utf-8"),
) as { headers: HeaderRule[] };

function sitewide(): Record<string, string> {
  const rule = config.headers.find((r) => r.source === "/(.*)");
  if (!rule) throw new Error("vercel.json has no site-wide header rule");
  return Object.fromEntries(rule.headers.map((h) => [h.key.toLowerCase(), h.value]));
}

describe("static header baseline (vercel.json)", () => {
  it("stops MIME sniffing and leaks no full referrer cross-origin", () => {
    const h = sitewide();
    expect(h["x-content-type-options"]).toBe("nosniff");
    expect(h["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  });

  it("forbids framing, by both X-Frame-Options and CSP frame-ancestors", () => {
    const h = sitewide();
    expect(h["x-frame-options"]).toBe("DENY");
    expect(h["content-security-policy"]).toBe("frame-ancestors 'none'");
  });

  it("ships no full CSP: frame-ancestors is the only directive", () => {
    const csp = sitewide()["content-security-policy"];
    expect(csp.split(";").map((d) => d.trim().split(/\s+/)[0])).toEqual(["frame-ancestors"]);
  });
});
