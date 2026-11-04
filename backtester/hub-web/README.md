# Backtester — the web front end, retired from the hub

These files are the Backtester tool that `william.revah.paris` served at
`/tools/backtester/` and `/en/tools/backtester/` until 2026-10-11, when the hub
took it off the site. They are kept here, next to the service they called, so
the tool can come back without archaeology.

**They are not built or run from this repo.** midas's Astro build only includes
`site/src/**`, and nothing here is on the import graph of `backtester.app`.
Every file is a byte-for-byte copy of the hub at the tag below, except the two
extracts marked as such.

## Where each file came from

Hub repo: `w2ur/william-revah-paris`, tag **`backtester-last`** (commit
`2ce3d9c`, the last commit with the tool on the site). Paths below are
relative to the hub root and to this directory alike.

| file here | former hub path | note |
|---|---|---|
| `src/pages/tools/backtester.astro` | `src/pages/tools/backtester.astro` | FR page |
| `src/pages/en/tools/backtester.astro` | `src/pages/en/tools/backtester.astro` | EN page |
| `src/lib/backtester-island.ts` | `src/lib/backtester-island.ts` | the client island (uPlot chart) |
| `src/lib/backtester.ts` | `src/lib/backtester.ts` | pure types + payload builder, mirrors `backtester/schemas.py` |
| `src/lib/backtester.test.ts` | `src/lib/backtester.test.ts` | its unit tests (`tsx --test`) |
| `src/lib/backtesterCatalog.ts` | `src/lib/backtesterCatalog.ts` | server-only catalog loader |
| `src/data/backtester-catalog.json` | `src/data/backtester-catalog.json` | committed catalog fallback |
| `src/i18n/tool-strings.ts` | `src/i18n/tool-strings.ts` | whole file; `backtesterStrings` is the part that was the tool's (the Conversations strings stayed on the hub) |
| `scripts/backtester-catalog.mjs` | `scripts/backtester-catalog.mjs` | pure catalog validator + date re-stamp |
| `scripts/backtester-catalog.test.mjs` | `scripts/backtester-catalog.test.mjs` | its tests (`node --test`) |
| `scripts/build-prefetch.backtester.mjs` | extract of `scripts/build-prefetch.mjs` (lines 181-233) | not a runnable module — see its header |
| `netlify/functions/backtest.mts` | `netlify/functions/backtest.mts` | the `/api/backtest/{catalog,run}` proxy |
| `test/backtest.test.ts` | `test/backtest.test.ts` | the proxy's tests |
| `backtester.css` | extract of `src/styles/style.css` (lines 2821-2896) | the `.bt-*` and uPlot re-token block |

Hub-side wiring that was removed rather than copied, all visible at the tag:
the `/api/backtest/*` rewrites in `netlify.toml`, the `uplot` dependency in
`package.json`, the route pair in `src/i18n/counterpart.ts`, the `backtester`
entry in `src/data/editorial.ts`, and the `BACKTESTER_URL` /
`BACKTESTER_SECRET` Netlify environment variables.

## The API

The Cloud Run service in this directory's parent, `backtester/` (FastAPI,
`backtester/app.py`), was its API: the Netlify function proxied
`GET /catalog` and `POST /run` to it, injecting `X-Backtester-Secret`. With the
front end retired, **that service has no consumer**.
