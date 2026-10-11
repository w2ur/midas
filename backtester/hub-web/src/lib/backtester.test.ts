import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRunRequest, computeWakeupRetries, optionsDiffer, type Preset } from './backtester.ts';

const PRESET: Preset = {
  id: 'golden-cross-sp500',
  label: 'Golden Cross — S&P 500',
  selector: 'golden-cross',
  manager: 'trailing-stop',
  rules: { max_positions: 20, max_position_pct: 10, min_hold_days: 5 },
  default_universe: 'sp500',
};

test('buildRunRequest assembles a signal request from a preset', () => {
  const req = buildRunRequest(PRESET, 'dow30', '2018-01-01', '2024-01-01', 10000, 'EUR');
  assert.deepEqual(req, {
    kind: 'signal',
    config: {
      universe: 'dow30',
      selector: 'golden-cross',
      manager: 'trailing-stop',
      max_positions: 20,
      max_position_pct: 10,
      min_hold_days: 5,
    },
    start_date: '2018-01-01',
    end_date: '2024-01-01',
    capital: 10000,
    currency: 'EUR',
  });
});

test('buildRunRequest carries the chosen universe, not the preset default', () => {
  const req = buildRunRequest(PRESET, 'nasdaq100', '2020-01-01', '2021-01-01', 5000, 'USD');
  assert.equal(req.config.universe, 'nasdaq100');
  assert.equal(req.currency, 'USD');
  assert.equal(req.capital, 5000);
});

// Regression: the revalidation used to compare whole catalogs with
// JSON.stringify. `date_bounds.max` is upstream's "today" and the snapshot's
// build date, so the two disagree on any day the site was not rebuilt — and a
// spurious "differs" rebuilds the dropdowns, resetting a selection the visitor
// made while the request was still in flight.
const CATALOG = {
  presets: [PRESET],
  universes: [{ id: 'sp500', label: 'S&P 500' }],
  date_bounds: { min: '2010-01-01', max: '2026-07-28' },
  currencies: ['EUR', 'USD'] as Array<'EUR' | 'USD'>,
};

test('optionsDiffer ignores date_bounds', () => {
  const fresher = { ...CATALOG, date_bounds: { min: '2010-01-01', max: '2026-08-04' } };
  assert.equal(optionsDiffer(CATALOG, fresher), false);
});

test('optionsDiffer sees a new preset', () => {
  const grown = { ...CATALOG, presets: [PRESET, { ...PRESET, id: 'other' }] };
  assert.equal(optionsDiffer(CATALOG, grown), true);
});

test('optionsDiffer sees a relabelled universe', () => {
  const relabelled = { ...CATALOG, universes: [{ id: 'sp500', label: 'S&P 500 (US)' }] };
  assert.equal(optionsDiffer(CATALOG, relabelled), true);
});

test('optionsDiffer ignores a rules-only change — the dropdowns are unaffected', () => {
  const retuned = {
    ...CATALOG,
    presets: [{ ...PRESET, rules: { ...PRESET.rules, min_hold_days: 9 } }],
  };
  assert.equal(optionsDiffer(CATALOG, retuned), false);
});

test('computeWakeupRetries fits a 3-minute budget into 12 retries at a 15s interval', () => {
  assert.equal(computeWakeupRetries(180_000, 15_000), 12);
});

test('computeWakeupRetries floors a partial interval rather than rounding up', () => {
  assert.equal(computeWakeupRetries(29_999, 15_000), 1);
  assert.equal(computeWakeupRetries(14_999, 15_000), 0);
});

test('computeWakeupRetries never returns a negative count', () => {
  assert.equal(computeWakeupRetries(0, 15_000), 0);
  assert.equal(computeWakeupRetries(-5_000, 15_000), 0);
});

test('computeWakeupRetries treats a non-positive interval as no retries, not a crash', () => {
  assert.equal(computeWakeupRetries(180_000, 0), 0);
  assert.equal(computeWakeupRetries(180_000, -1), 0);
});
