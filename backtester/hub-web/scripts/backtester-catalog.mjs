// Pure helpers for the build-time backtester catalog snapshot. Single source of
// truth shared by the prefetch (build-prefetch.mjs) and its unit test. No deps.
//
// The snapshot exists so the tool's form paints complete on the first frame
// instead of two empty dropdowns waiting on a 2-5s Cloud Run cold start. That
// only holds if what we commit is structurally the same shape the island
// expects — hence the validation below, which gates both the live fetch and
// (implicitly, via the test) the committed fallback.

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

const isNonEmptyString = (v) => typeof v === 'string' && v.length > 0;
const isFiniteNumber = (v) => typeof v === 'number' && Number.isFinite(v);

/** @param {unknown} rules */
function hasValidRules(rules) {
  if (!rules || typeof rules !== 'object') return false;
  return (
    isFiniteNumber(rules.max_positions) &&
    isFiniteNumber(rules.max_position_pct) &&
    isFiniteNumber(rules.min_hold_days)
  );
}

/** @param {unknown} preset */
function isValidPreset(preset) {
  if (!preset || typeof preset !== 'object') return false;
  // `default_universe` is what the island uses to pre-select a universe when
  // the strategy changes; a preset without one silently breaks that pairing.
  const strings = ['id', 'label', 'selector', 'manager', 'default_universe'];
  return strings.every((k) => isNonEmptyString(preset[k])) && hasValidRules(preset.rules);
}

/** @param {unknown} universe */
function isValidUniverse(universe) {
  if (!universe || typeof universe !== 'object') return false;
  return isNonEmptyString(universe.id) && isNonEmptyString(universe.label);
}

/**
 * Structural check on a catalog payload. Never throws — a malformed upstream
 * response must degrade to the committed snapshot, not fail the build.
 * @param {unknown} data
 * @returns {boolean}
 */
export function isValidCatalog(data) {
  if (!data || typeof data !== 'object') return false;
  const { presets, universes, date_bounds: bounds, currencies } = data;
  if (!Array.isArray(presets) || presets.length === 0) return false;
  if (!presets.every(isValidPreset)) return false;
  if (!Array.isArray(universes) || universes.length === 0) return false;
  if (!universes.every(isValidUniverse)) return false;
  if (!bounds || typeof bounds !== 'object') return false;
  if (!ISO_DATE.test(bounds.min ?? '') || !ISO_DATE.test(bounds.max ?? '')) return false;
  if (!Array.isArray(currencies) || currencies.length === 0) return false;
  return true;
}

/**
 * Copy of `catalog` with `date_bounds.max` moved to `today`.
 *
 * Upstream's `max` is simply "today", so a committed snapshot's max goes stale
 * by exactly one day per day. Re-stamping it at build time is what keeps the
 * fallback honest: the end-date input offers every day up to the build, not up
 * to whenever the snapshot happened to be generated.
 *
 * @param {object} catalog
 * @param {string} today ISO YYYY-MM-DD
 * @returns {object}
 */
export function withMaxDate(catalog, today) {
  return {
    ...catalog,
    date_bounds: { ...catalog.date_bounds, max: today },
  };
}
