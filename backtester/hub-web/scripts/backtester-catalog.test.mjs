import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isValidCatalog, withMaxDate } from './backtester-catalog.mjs';

/** A minimal but complete payload, shaped like backtester/catalog.py's output. */
const valid = () => ({
  presets: [
    {
      id: 'rsi-contrarian-sp500',
      label: 'RSI Contrarian — S&P 500',
      selector: 'rsi-oversold',
      manager: 'equal-weight',
      rules: { max_positions: 15, max_position_pct: 10.0, min_hold_days: 3 },
      default_universe: 'sp500',
    },
  ],
  universes: [{ id: 'sp500', label: 'S&P 500' }],
  date_bounds: { min: '2010-01-01', max: '2026-07-28' },
  currencies: ['EUR', 'USD'],
});

/** Deep clone so a mutation in one case cannot leak into the next. */
const mutate = (fn) => {
  const c = structuredClone(valid());
  fn(c);
  return c;
};

test('isValidCatalog accepts a complete payload', () => {
  assert.equal(isValidCatalog(valid()), true);
});

test('isValidCatalog rejects an empty preset list', () => {
  assert.equal(isValidCatalog(mutate((c) => { c.presets = []; })), false);
});

test('isValidCatalog rejects a preset missing default_universe', () => {
  assert.equal(isValidCatalog(mutate((c) => { delete c.presets[0].default_universe; })), false);
});

test('isValidCatalog rejects a preset whose rules.min_hold_days is a string', () => {
  assert.equal(isValidCatalog(mutate((c) => { c.presets[0].rules.min_hold_days = '3'; })), false);
});

test('isValidCatalog rejects an empty universe list', () => {
  assert.equal(isValidCatalog(mutate((c) => { c.universes = []; })), false);
});

test('isValidCatalog rejects a universe missing its label', () => {
  assert.equal(isValidCatalog(mutate((c) => { delete c.universes[0].label; })), false);
});

test('isValidCatalog rejects a malformed date_bounds.min', () => {
  assert.equal(isValidCatalog(mutate((c) => { c.date_bounds.min = '01/01/2010'; })), false);
});

test('isValidCatalog rejects an empty currency list', () => {
  assert.equal(isValidCatalog(mutate((c) => { c.currencies = []; })), false);
});

test('isValidCatalog rejects non-objects instead of throwing', () => {
  for (const bad of [null, undefined, 'catalog', 42, []]) {
    assert.equal(isValidCatalog(bad), false);
  }
});

test('withMaxDate replaces max and leaves min untouched', () => {
  const out = withMaxDate(valid(), '2027-01-15');
  assert.equal(out.date_bounds.max, '2027-01-15');
  assert.equal(out.date_bounds.min, '2010-01-01');
});

test('withMaxDate leaves every other field untouched', () => {
  const input = valid();
  const out = withMaxDate(input, '2027-01-15');
  assert.deepEqual(out.presets, input.presets);
  assert.deepEqual(out.universes, input.universes);
  assert.deepEqual(out.currencies, input.currencies);
});

test('withMaxDate does not mutate its input', () => {
  const input = valid();
  withMaxDate(input, '2027-01-15');
  assert.equal(input.date_bounds.max, '2026-07-28');
});

test('withMaxDate output still validates', () => {
  assert.equal(isValidCatalog(withMaxDate(valid(), '2027-01-15')), true);
});
