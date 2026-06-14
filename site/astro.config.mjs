import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://midas.revah.paris",
  output: "static",
  build: { format: "directory" },
  trailingSlash: "ignore",
  // /about was folded into /methodology (2026-06-14). Keep old/external links alive.
  redirects: {
    "/about": "/methodology",
  },
});
