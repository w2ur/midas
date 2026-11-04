// Server-only loader for the backtester catalog. Mirrors lib/lettre.ts:
// read the build-time snapshot, degrade gracefully.
//
// NEVER import this from the client island — it pulls in node:fs. The island
// reads the same catalog from the inlined `#bt-catalog` JSON block instead.

import committed from '../data/backtester-catalog.json';
import type { Catalog } from './backtester';
import { readBuildData } from './buildData';

/**
 * The catalog written by scripts/build-prefetch.mjs (live upstream when it
 * answered, the committed snapshot otherwise). Falls back to the committed
 * snapshot if the prefetch never ran — `astro dev` on a fresh clone, say.
 */
export function loadCatalog(): Catalog {
  return readBuildData<Catalog>('data/live/backtester-catalog.json') ?? (committed as Catalog);
}
