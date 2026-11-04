// Fragment, not a runnable module: the Backtester half of the hub's
// scripts/build-prefetch.mjs (hub tag backtester-last, lines 181-233).
// It relied on that file's imports (fs, path, isValidCatalog, withMaxDate)
// and its helpers (writeJson, PUBLIC_LIVE, CATALOG_SNAPSHOT); main() called
// writeBacktesterCatalog() between the Midas board and the Tsundoku shelf.

// The Cloud Run backtester sleeps between requests, so the catalog route can
// take seconds to answer — or never answer at all. The build must not wait on
// it: on any failure we fall back to the committed snapshot.
async function fetchBacktesterCatalog() {
  const base = process.env.BACKTESTER_URL;
  const secret = process.env.BACKTESTER_SECRET;
  if (!base || !secret) {
    console.log('[prefetch] Backtester: (no credentials — using committed snapshot)');
    return null;
  }
  try {
    const res = await fetch(`${base}/catalog`, {
      headers: { 'X-Backtester-Secret': secret },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) {
      console.warn(`[prefetch] Backtester catalog: HTTP ${res.status}`);
      return null;
    }
    const data = await res.json();
    if (!isValidCatalog(data)) {
      console.warn('[prefetch] Backtester catalog: malformed payload');
      return null;
    }
    return data;
  } catch (e) {
    console.warn('[prefetch] Backtester catalog failed:', e.message);
    return null;
  }
}

/** ISO YYYY-MM-DD for the current build, used to re-stamp the snapshot's max date. */
function buildDate() {
  return new Date().toISOString().slice(0, 10);
}

// Writes the catalog the two backtester pages inline at build time. Live when
// the upstream answers, the committed snapshot (re-stamped to today) otherwise.
// Wrapped whole so even a missing/unreadable snapshot cannot fail the build.
async function writeBacktesterCatalog() {
  try {
    const live = await fetchBacktesterCatalog();
    const snapshot = JSON.parse(await fs.readFile(CATALOG_SNAPSHOT, 'utf-8'));
    const catalog = live ?? withMaxDate(snapshot, buildDate());
    await writeJson(path.join(PUBLIC_LIVE, 'backtester-catalog.json'), catalog);
    console.log(
      `[prefetch] Backtester catalog: ${live ? 'live' : 'committed snapshot'}` +
        ` — ${catalog.presets.length} presets, max ${catalog.date_bounds.max}`,
    );
  } catch (e) {
    console.warn('[prefetch] Backtester catalog: not written —', e.message);
  }
}
