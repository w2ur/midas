// Client island for the backtester tool. Lives here rather than inline in the
// two page files because FR and EN ship byte-identical behaviour — a duplicated
// island is how the two languages silently drift apart.
// Strings come from the inlined `#bt-i18n` JSON and the catalog from the inlined
// `#bt-catalog` JSON, so this module stays language-agnostic and never blocks
// first paint on the network.

import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import {
  buildRunRequest,
  computeWakeupRetries,
  optionsDiffer,
  type Catalog,
  type Preset,
  type RunResponse,
} from './backtester.ts';
import { watchTheme } from './theme-watch.ts';

interface IslandStrings {
  warmingUp: string;
  wakingUp: string;
  unavailable: string;
  running: string;
  networkError: string;
  genericError: string;
  strategySeries: string;
  totalReturn: string;
  cagr: string;
  sharpe: string;
  maxDrawdown: string;
  vsBenchmark: string;
}

// The backend cold-starts in 130-230s (measured from Cloud Run's own logs),
// well past Netlify's ~30s function timeout — see `computeWakeupRetries` in
// backtester.ts for the full reasoning. Retry a plain, fixed interval rather
// than backing off: the cold start is a roughly-known constant, not an
// escalating failure.
//
// The budget MUST exceed the slowest cold start observed, or the retry loop
// gives up on exactly the case it exists to survive: 232s was measured, so a
// 180s budget would have failed that request. 300s buys margin over the worst
// case seen rather than over the median.
//
// Elapsed wall-clock runs longer than the budget, and deliberately so: a
// failing attempt is not instant, it hangs until Netlify kills it (~30s), so
// the real window is the sleeps plus up to 30s per attempt. Each of those
// attempts is a Netlify function invocation billed for its full ~30s — the
// cost is bounded and only paid by a visitor who arrives on a cold service,
// which is what shrinking the container image (the real fix) removes.
const WAKEUP_BUDGET_MS = 300_000;
const WAKEUP_INTERVAL_MS = 20_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Read a theme custom property off <body> at render time. Light/dark redefine
 * these on `:root[data-theme]` (or via `prefers-color-scheme` when no explicit
 * choice is stored), so the value is only knowable at runtime — the fallback
 * covers the case where the stylesheet has not applied yet.
 */
function token(name: string, fallback: string): string {
  return getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
}

/** The page's own body font family at uPlot's default axis size. */
function axisFont(): string {
  return `12px ${getComputedStyle(document.body).fontFamily || 'system-ui, sans-serif'}`;
}

/** Parse the inlined catalog block. Null (rather than a throw) if it is absent or malformed. */
function parseCatalog(raw: string | null | undefined): Catalog | null {
  try {
    const data = JSON.parse(raw ?? '') as Catalog;
    return Array.isArray(data?.presets) && data.presets.length > 0 ? data : null;
  } catch {
    return null;
  }
}

export function initBacktester(): void {
  const i18nEl = document.getElementById('bt-i18n');
  const t = JSON.parse(i18nEl?.textContent ?? '{}') as IslandStrings;

  const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
  const presetSel = $<HTMLSelectElement>('bt-preset');
  const universeSel = $<HTMLSelectElement>('bt-universe');
  const startI = $<HTMLInputElement>('bt-start');
  const endI = $<HTMLInputElement>('bt-end');
  const capitalI = $<HTMLInputElement>('bt-capital');
  const currencySel = $<HTMLSelectElement>('bt-currency');
  const form = $<HTMLFormElement>('bt-form');
  const fieldset = $<HTMLFieldSetElement>('bt-fields');
  const runBtn = $<HTMLButtonElement>('bt-run');
  const status = $<HTMLParagraphElement>('bt-status');
  const chartEl = $<HTMLDivElement>('bt-chart');
  const metricsEl = $<HTMLDivElement>('bt-metrics');
  const warningsEl = $<HTMLUListElement>('bt-warnings');

  // The catalog is snapshotted at build time and inlined next to the strings,
  // so the form is already complete and selectable on the first tick.
  let catalog: Catalog | null = parseCatalog(document.getElementById('bt-catalog')?.textContent);
  let chart: uPlot | null = null;
  // Kept so the chart can be redrawn from the same data when the theme changes.
  let lastRun: RunResponse | null = null;

  // The run button's own label, kept so a later success can put it back — the
  // failure branch below overwrites it with the "unavailable" string.
  const runLabel = runBtn.textContent ?? '';

  // The form ships disabled and the status line ships with `warmingUp` already
  // in it (both server-rendered), so a click can never be swallowed while the
  // service is still cold and there is no text-injection jump on hydration.
  // Warm the instance via the catalog route while the user reads the form —
  // and revalidate the snapshot against what upstream serves.
  //
  // The first call can (and, on a cold backend, does) time out at Netlify's
  // proxy after ~30s while the request has already reached and is waking the
  // upstream service — so a bare failure here does not mean the service is
  // actually unavailable, only that this particular call did not wait long
  // enough. `wakeCatalog` keeps the fieldset disabled and retries at a fixed
  // interval for a few minutes before giving up for good.
  void wakeCatalog();

  async function wakeCatalog(): Promise<void> {
    const retries = computeWakeupRetries(WAKEUP_BUDGET_MS, WAKEUP_INTERVAL_MS);
    for (let attempt = 0; attempt <= retries; attempt++) {
      if (attempt > 0) {
        status.textContent = t.wakingUp;
        await sleep(WAKEUP_INTERVAL_MS);
      }
      let r: Response;
      try {
        r = await fetch('/api/backtest/catalog');
      } catch {
        continue;
      }
      if (!r.ok) continue;
      const live = (await r.json()) as Catalog;
      // Rebuild the option lists only when the *dropdowns* genuinely changed —
      // see `optionsDiffer`. An identical catalog must not churn the DOM or reset
      // a selection the visitor has already made while the request was in flight.
      // The live catalog is adopted either way, so a rules-only change upstream
      // still reaches `buildRunRequest` without touching the DOM.
      const changed = !catalog || optionsDiffer(live, catalog);
      // Adopt before rendering: fillOptions -> syncUniverseDefault resolves the
      // newly-selected preset against `catalog`, which must already be the live one.
      catalog = live;
      if (changed) fillOptions(live);
      // The bounds are re-stamped daily, so always trust the live ones.
      startI.min = endI.min = live.date_bounds.min;
      startI.max = endI.max = live.date_bounds.max;
      runBtn.textContent = runLabel;
      fieldset.disabled = false;
      status.textContent = '';
      return;
    }
    markUnavailable();
  }

  /** Service did not answer: say so on the button as well as in the status line. */
  function markUnavailable() {
    status.textContent = t.unavailable;
    runBtn.textContent = t.unavailable;
    fieldset.disabled = true;
  }

  function fillOptions(c: Catalog) {
    presetSel.replaceChildren(...c.presets.map((p) => new Option(p.label, p.id)));
    universeSel.replaceChildren(...c.universes.map((u) => new Option(u.label, u.id)));
    syncUniverseDefault();
  }

  const presetById = (id: string): Preset =>
    catalog!.presets.find((p) => p.id === id)!;

  function clearResults() {
    if (chart) { chart.destroy(); chart = null; }
    lastRun = null;
    metricsEl.innerHTML = '';
    warningsEl.innerHTML = '';
  }

  // The chart's strokes are read from CSS custom properties at render time, and
  // uPlot cannot be told to re-read them — so a scheme change means destroy +
  // rebuild on either source of a scheme change (see theme-watch.ts).
  // No chart yet, nothing to redraw.
  watchTheme(() => { if (lastRun) render(lastRun); });

  function syncUniverseDefault() {
    if (!catalog) return;
    universeSel.value = presetById(presetSel.value).default_universe;
  }
  presetSel.addEventListener('change', syncUniverseDefault);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    // Unreachable while the fieldset is disabled, but a bare `return` here is
    // exactly the silently-swallowed click this phase removed — keep it noisy.
    if (!catalog) { status.textContent = t.unavailable; return; }
    form.setAttribute('aria-busy', 'true');
    status.textContent = t.running;
    const payload = buildRunRequest(
      presetById(presetSel.value),
      universeSel.value,
      startI.value,
      endI.value,
      Number(capitalI.value),
      currencySel.value as 'EUR' | 'USD',
    );
    let res: Response;
    try {
      res = await fetch('/api/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch {
      clearResults();
      status.textContent = t.networkError;
      form.setAttribute('aria-busy', 'false');
      return;
    }
    form.setAttribute('aria-busy', 'false');
    if (!res.ok) {
      clearResults();
      const err = await res.json().catch(() => ({}));
      status.textContent = typeof err.detail === 'string' ? err.detail : t.genericError;
      return;
    }
    status.textContent = '';
    render((await res.json()) as RunResponse);
  });

  function render(data: RunResponse) {
    lastRun = data;
    const dates = data.equity_curve.map((p) => Date.parse(p.date) / 1000);
    const equity = data.equity_curve.map((p) => p.value);
    const bench = data.benchmark_curve.length
      ? data.benchmark_curve.map((p) => p.value)
      : null;
    // Every colour comes from the active theme's tokens — the chart used to be
    // hardcoded GitHub blue/grey, unreadable on the near-black échiquier page.
    const accent = token('--accent', '#4C1C00');
    // Les trois replis suivent la palette du 2026-09-16 au meme titre que
    // l'accent. Une seule des quatre lignes avait ete reprise : c'est
    // exactement la demi-passe que cette branche reproche aux revisions
    // precedentes, et elle est invisible pour le test de contraste, qui ne lit
    // que les trois blocs de jetons de `style.css`.
    const muted = token('--text-muted', '#726453');
    const gridStroke = token('--border-thin', 'rgba(36, 25, 15, 0.16)');
    const tickStroke = token('--border-medium', 'rgba(36, 25, 15, 0.30)');
    const font = axisFont();
    const series: uPlot.Series[] = [
      {},
      { label: t.strategySeries, stroke: accent, width: 2 },
    ];
    const cols: (number[] | null)[] = [dates, equity];
    if (bench) { series.push({ label: data.benchmark_label, stroke: muted, width: 1 }); cols.push(bench); }
    // One object per axis, never a shared reference: uPlot mutates the config
    // it is handed (side, _el, _size, font…), so two axes sharing one object
    // would both end up on the same edge of the chart.
    const axis = (): uPlot.Axis => ({
      stroke: muted,
      font,
      grid: { stroke: gridStroke, width: 1 },
      ticks: { stroke: tickStroke, width: 1 },
    });
    if (chart) chart.destroy();
    chart = new uPlot(
      {
        width: chartEl.clientWidth || 720,
        height: 320,
        series,
        axes: [axis(), axis()],
        scales: { x: { time: true } },
      },
      cols as uPlot.AlignedData,
      chartEl,
    );
    const m = data.metrics;
    metricsEl.innerHTML = [
      [t.totalReturn, `${m.total_return_pct.toFixed(1)} %`],
      [t.cagr, `${m.cagr_pct.toFixed(1)} %`],
      [t.sharpe, m.sharpe.toFixed(2)],
      [t.maxDrawdown, `${m.max_drawdown_pct.toFixed(1)} %`],
      [t.vsBenchmark, `${m.vs_msci_world_pct.toFixed(1)} %`],
    ].map(([k, v]) => `<span class="bt-metric"><b>${v}</b>${k}</span>`).join('');
    warningsEl.innerHTML = data.warnings.map((w) => `<li>⚠︎ ${w}</li>`).join('');
  }
}
