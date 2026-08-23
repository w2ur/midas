---
name: midas-ci-guards
description: The CI gate and the guard discipline behind it — why the aggregate gate asserts a named list, branch protection on this public repo, path-filtering at the workflow rather than the job, warnings-as-errors with its three named third-party exceptions, the rule that every guard must be able to fail and must have a consumer, push-with-retry for scheduled writers, attest-ledger's tag dating, sync_core.check()'s two tiers, and failure-issue alerting. Load before editing anything under .github/workflows/, .github/actions/, scripts/check_*.py, scripts/sync_core.py, or pyproject.toml's warning filters.
---

# CI, gates and guards

Moved out of `CLAUDE.md` on 2026-08-23. Nothing was cut in the move.

**The gate is advisory on this repo, and the 2026-08-21 flip to public did not
change that** — only the reason. Rulesets were *unavailable* while the repo was
private (measured: `gh api repos/w2ur/midas/branches/main/protection` → 403
`Upgrade to GitHub Pro or make this repository public`). Going public lifts that,
but there is still no workable ruleset, and both halves of that were **measured on
a throwaway `ruleset-probe` branch on 2026-08-21 rather than reasoned about**:
- `enforcement: "evaluate"` — the read-only mode that would let a rule be observed
  before it bites — returns **422 `Enforcement evaluate option is not supported on
  this plan. Please upgrade to Enterprise`**. So the cautious path does not exist
  here. Do not write a plan around it; this one did, for a morning.
- `enforcement: "active"` with a `required_status_checks` rule on `gate` **rejects
  a direct push** — `GH013 … Required status check "gate" is expected.` A branch
  *creation* still passes, which makes the first probe look like a false negative;
  push an **update** to see the rule fire. On `main` that is the daily session's
  `git push origin HEAD:main` and all six scheduled writers, i.e. the desk.
- Adding `{"actor_id": 5, "actor_type": "RepositoryRole"}` as a bypass actor lets
  the **owner's** push through, printing the violation as a warning instead of
  blocking — the nearest thing to evaluate mode this plan has. But scheduled
  workflows push as `github-actions[bot]`, an **Integration**, not a repository
  role, so they are not covered by it and would still be rejected. Untested,
  because testing it means risking a real session push.
So the honest position is that a `main` ruleset here either stops the desk or
exempts everyone who actually pushes. If one is ever created it must require the
`gate` check **only**, never the individual jobs — a rule can only require a name
it already knows, which is the same hole `EXPECTED` closes one level down.

Neither suite is path-filtered per job — the site suite reads committed engine
artifacts (the OHLCV store, `data/ticker_currencies.json`, METHODOLOGY.md
anchors), so "site tests only matter when `site/**` changes" is false. Both had
coverage that existed but never executed: the site suite had no CI job at all
(its double-divide regression and the WCAG contrast guard were green by never
running), and `backtester/tests` sat outside `testpaths`. A testpaths entry
matching nothing is skipped silently, which is what lets this same
`pyproject.toml` sync to midas-core, where no `backtester/` exists.

**The jobs are not path-filtered; the workflow is** (`paths-ignore: data/**`),
and that gap has teeth because the site suite *reads* `data/`. A session
commit's only footprint is `data/`, so the one commit class that changes what
the site suite asserts is the one class that never runs it. Until 2026-08-07
`site/tests/cadence.test.ts` pinned exact literals (216 fills / 87 sessions /
58 fill-days / a per-agent map / a fixed `asOf`), so every session that filled
a trade left `main` red — invisibly, until some unrelated PR ran `pull_request`
(which has no path filter) and failed for reasons that had nothing to do with
it. That is what happened to the 08-07 session: `main` sat red on `npm test`
with nothing reporting it. The pins are now **floors**, matching what
`tests/test_rails_live_coverage.py` already did for the same ledger
(`assert len(fills) > 100`). This is not a weaker assertion of the same thing —
it is the right assertion: the ledger only grows, so a *decrease* is the real
failure mode, and it has occurred (the 2026-05-18..23 `commit_and_push`
pathspec bug silently dropped fills for two months). Per-agent floors are kept
alongside the roster-wide one because a session that adds fills to one book
while dropping them from another leaves the total flat. Verified by deleting a
single fill from `world`'s ledger: three tests fire. The pins' original
rationale — "so the prose gets regenerated rather than going stale" — no longer
applies at all: every consumer (`PreRegistrationStatus`, `MethodologyFacts`,
`oss-stats`, the homepage) calls `cadenceStats()` at build time, so no cadence
figure is transcribed anywhere.

**Warnings are errors, with three named third-party exceptions** (`pyproject.toml`
`filterwarnings`, 2026-08-07). The zero-warnings policy was being asserted, not
enforced — 69 warnings had accumulated. `filterwarnings = ["error", …]` means any
warning not on the list fails the suite; the three that are listed are
unfixable from here (starlette's `TestClient` via `fastapi.testclient`,
`pandas_ta` setting the deprecated `mode.copy_on_write` at import, and `bt`'s
own chained assignment inside `get_transactions`). Matching is on the **message,
not the module**, so an entry stops applying if upstream changes its text — a
stale ignore surfaces as a red run rather than as silent over-suppression.
**A hard gate on warnings is normally fragile and is safe here only because
`requirements.txt` is a full lockfile**: a new third-party warning can arrive
only with a deliberate lock bump. It caught a real defect on its first run —
`test_strategy_specs.py` leaked 15 file handles via `json.load(open(path))`.


**Every guard must be able to fail, and must have a consumer** (2026-08-07, W2.10). Two standing rules, both learned the expensive way — the global "a check that has never produced the opposite answer is not evidence" rule applied to *analyses* here for months while the *standing infrastructure* went unaudited, and eleven guards turned out to be dark or vacuous at once.
1. **Before a guard counts as shipped it must have demonstrably failed**: a historical red, a unit test of its own check logic, or a recorded forced-failure drill. A guard with zero lifetime failures and no self-test is unproven infrastructure, not protection. Where a guard cannot run everywhere it matters (`test_ci_guards.TestRegressionCitations` needs git history that CI's `fetch-depth: 1` does not have), say so in its docstring rather than letting the green tick imply coverage it does not have.
2. **A red X is not a consumer.** Anything scheduled routes failure through `.github/actions/failure-issue`, because `core-drift-guard` was red three consecutive Mondays with nobody reading it, and the watchdog correctly caught the 2026-07-31 miss into an inbox nobody opened.

**Scheduled writers push through `.github/actions/push-with-retry`** (2026-08-07, W2.6). `fetch-ohlcv`, `fetch-sentiment`, `refresh-universes` and `resweep-held-tickers` used a bare `git push`, so when two collided the loser's commit died with the runner. Their nominal cron separation guarantees nothing — GitHub's scheduler is never on time: measured across this repo's run history it **typically lands 40 min to 2 h 45 late, with a tail past 5 h** (worst observed: a `fetch-sentiment` run ~5 h 43 late on 2026-08-07). Cite that characterisation, typical *and* tail; the figures had drifted into three incompatible versions across four files by 2026-08-12, and a scheduling decision rides on which one you believe. The action stages, commits, and pushes `HEAD:main` with a rebase-and-retry loop, matching what `scripts/refresh_leaderboard._push_with_rebase_retry` and `check_triggers` already did in Python. It rebases rather than merges, so the history that `git log --grep='[restate]'` and `session-integrity` read stays linear. **`resweep-held-tickers` is the one that most needs it**: its commit can carry a `PortfolioManager.apply_split` correction to real holdings, and a lost push discards that for a week.

**It drops pathspecs that match nothing before staging** (2026-08-10). `git status` and `git add` disagree about an absent path: `status` exits 0 and reports nothing for it, `add` exits 128 with `pathspec ... did not match any files`. So the action's "nothing to commit" guard passed and the stage below it died under `set -e`, taking the run with it. `fetch-ohlcv` passes `data/market/quarantine/`, which exists only once the ingest tripwire has actually refused a row — i.e. never on a healthy night — so its 2026-08-08 and 08-09 runs fetched cleanly (`34 symbols, 0 failures`) and threw the result away. The filter keys on **the worktree OR the index**, mirroring exactly when `git add` succeeds, so staging the deletion of a whole tracked directory still works; filtering on `-e` alone would silently skip it. **`data/market/quarantine/` stopped being the only caller path that can be absent on 2026-08-11** (`761c60382`, a quarantined MNST print — **a real 2:1 split effective 2026-08-11, confirmed against Yahoo's own split calendar on 2026-08-17, not the "2x vendor bad tick" this line called it until then**; no book holds it): the directory is now committed and tracked forever, so it can never again be the one skipped by this filter. `tests/test_ci_guards.py`'s `OPTIONAL_PUSH_PATHS` exemption for it (and the control test that verified the exemption still covered something) were retired in the same commit that dropped it — a guard's own assertion had named the remedy in advance. The filtering mechanism above stays: it's generic to any pathspec, not to quarantine specifically, and covers the case again the day a new caller path is legitimately absent.


**Scheduled workflows alert through `.github/actions/failure-issue`.** A red X
plus a failure email is not a closed loop — `core-drift-guard` was red three
consecutive Mondays while the public mirror shipped stale engine code. The
composite action files a GitHub issue on failure, comments on the existing one
instead of filing duplicates (idempotent per *cause*, not per date — a job
failing five days running is one fact), and closes it on the next success.
Wired into `core-drift-guard`, `fetch-ohlcv`, `fetch-sentiment`,
`refresh-universes`, `resweep-held-tickers`, and **`session-integrity`**;
`session-watchdog` keeps its own per-date variant because each missed session is
a separate fact.

`session-integrity` is the one that is not scheduled, and it was added on
2026-08-07 for the same reason the others were: it went red on main that
evening and produced nothing but an X, found only because someone asked an
unrelated question about the merge. Its three jobs read committed data, so
silence there is the most expensive kind. It reports **once, from a trailing
`alert` job** that `needs` the other three — a run where all three fail is
still one fact, and the action is idempotent per cause, not per job. That job's
own `job.status` is always `success` (it only reports), so the outcome is
aggregated from `needs.*.result`; `tests/test_ci_guards.py` asserts the
aggregation and the `needs` list, and all three of those assertions were
confirmed capable of failing by breaking them one at a time.

