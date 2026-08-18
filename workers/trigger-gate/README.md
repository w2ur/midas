# trigger-gate

A Cloudflare Worker that decides, hourly, whether `check-triggers-crypto.yml` is
worth a GitHub Actions run. It reads every pending conditional order in one
GraphQL call, prices the crypto ones off Coinbase's public spot endpoint, and
`workflow_dispatch`es the workflow **only when a trigger is at or near its
level**. It never fills, retires or rails anything — the dispatched workflow
runs the real `scripts/check_triggers.py` with every rail intact.

## Why

The workflow's own hourly cron billed ~2 minutes per run — 23 runs/day, jobs
measured 58–71s astride GitHub's round-up-to-the-whole-minute billing edge — to
do ~5 seconds of work, projecting ~1,150–1,200 of the account's 2,000 monthly
minutes (measured 2026-08-18). Reading and comparing are cheap; only *firing*
needs a runner.

## Deploy

```bash
cd workers/trigger-gate
npx wrangler@4 deploy
npx wrangler@4 secret put GITHUB_PAT   # paste at the prompt — never into a file
```

`GITHUB_PAT` is a fine-grained token scoped to `w2ur/midas` only, with
**Contents: read**, **Actions: read and write**, **Issues: read and write**.
Contents reads the pending orders, Actions posts the dispatch, Issues is how the
Worker reports its own failures.

**The PAT's expiry is a dead-man switch.** When it dies the Worker fails *and
cannot self-report*, because the failure issue is filed with the same dead
credential. Keep the renewal date in a calendar.

## Degradation

If the Worker is dead, misconfigured, or simply never dispatches, crypto
conditional orders fall back to the daily 13:00 UTC `check-triggers` sweep —
which evaluates every asset class and is the sole owner of expiry. Nothing is
lost; only intraday granularity is. That is why an isolate-level death, where
no handler code runs and nothing can self-report, is an accepted risk.

The gate is deliberately allowed to **over**-dispatch and never to
**under**-dispatch: `TOLERANCE` widens the trigger band on the loose side only,
because Coinbase spot and the workflow's ccxt `last` are two different numbers.
A false positive costs one ~1-minute run that finds nothing; a false negative
delays a fill by up to a day.

## Alerting

On any failure the Worker files — or comments on — a GitHub issue titled
`trigger-gate worker failing` in `w2ur/midas`, then rethrows so the invocation
also shows as failed under Workers → midas-trigger-gate → Settings → Trigger
Events. One issue per cause, not one per occurrence.

## Tests

```bash
node --test 'workers/trigger-gate/test/*.test.mjs'   # gate logic, zero deps
pytest tests/test_trigger_gate_parity.py             # the Python↔JS parity guard
```

`src/gate.js` deliberately duplicates rules that live in `engine/triggers.py`
(the crypto allowlist, the expiry comparison). A JS copy of a Python rule is how
this repo's quote-currency defect happened, so the copy is pinned:
`tests/test_trigger_gate_parity.py` fails if the two drift. Change both in the
same commit.
