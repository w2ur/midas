---
name: midas-ci-guards
description: The CI gate and the guard discipline behind it — why the aggregate gate asserts a named list, branch protection on this public repo, path-filtering at the workflow rather than the job, warnings-as-errors with its three named third-party exceptions, the rule that every guard must be able to fail and must have a consumer, push-with-retry for scheduled writers, attest-ledger's tag dating, sync_core.check()'s two tiers, and failure-issue alerting. Load before editing anything under .github/workflows/, .github/actions/, scripts/check_*.py, scripts/sync_core.py, or pyproject.toml's warning filters.
---

# CI, gates and guards

Moved out of `CLAUDE.md` on 2026-08-23. Nothing was cut in the move.

**The gate is advisory on this repo BY DECISION, and the 2026-08-21 flip to public
did not change that decision** — only the reason (a required check WAS in force
from late August until its removal; see the 2026-09-04 addendum below). Rulesets were *unavailable* while the repo was
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

**2026-09-04 addendum.** Classic branch protection with a required `gate` status
check got created on `main` after the 2026-08-21 flip to public. `enforce_admins:
false` spared only the owner — `github-actions[bot]` is an Integration, not an
admin, and every scheduled writer's push to `main` is rejected with `GH006`/`Required
status check "gate" is expected` (first seen 2026-08-25 on the crypto watcher; a
run with nothing to push still passes), the commit lost with the runner — which
a watcher push can no longer be, see the `triggers/*` fallback under Conditional
Triggers. The required check was removed on 2026-09-04 (`gh api -X DELETE
repos/w2ur/midas/branches/main/protection/required_status_checks`; force-push
and deletion protection stay) and the next hosted `fetch-ohlcv` run pushed on
its first attempt. `gate` is advisory-only again. **A required check on `main`
is not survivable by the watcher fallback either**: `auto-merge-session.yml`
pushes main with `GITHUB_TOKEN`, the same refused push, so under a repeat the
fills stay durable on their `triggers/*` branches and the watcher refuses to
evaluate until a human merges — visibility, not self-healing. No PAT.

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


**`auto-merge-session.yml` takes two kinds of branch, and a `GITHUB_TOKEN` push cannot trigger it** (2026-09-05). Session commits on `claude/**` arrive by `on: push` because the sandbox pushes with its own credential. The watcher's `triggers/**` fallback branches (a fill `main` refused — see the `midas-session-cadence` skill for the push path) are pushed with `GITHUB_TOKEN`, and GitHub creates no workflow run for events that token causes, with `workflow_dispatch` and `repository_dispatch` as the only exceptions — so both watcher workflows end with `.github/actions/merge-fallback-branches`, which dispatches the merge for every `triggers/*` branch on origin, under `if: always()` and with `actions: write`. The two kinds pass different rules (session artifact rules vs. the watcher's commit-prefix + path allow-list, which refuses an empty diff for the same zero-coverage reason the `gate` refuses an empty `EXPECTED`), share the merge-and-delete mechanics, and only the watcher kind reports through `failure-issue` — the session kind is covered by `session-watchdog`, and the reporter is gated on the kind so a green session merge cannot close the watcher's issue. `tests/test_ci_guards.py` runs the scope script, the dispatch script and the gate script against fixtures rather than reading them.

**Four more rules on the watcher-merge path, from the first review of that work (2026-09-05)** — rules 1 and 2 were then narrowed and widened respectively by the second review; see the paragraph after this one. (1) **Blackout-aware**: the merge *is* the push to main, so the step "Defer inside the session window" exits 0 and keeps the branch inside the session window — read by regex from `SESSION_START`/`BLACKOUT_END` in `scripts/check_triggers.py`, never typed into the workflow (importing the module needs the venv the merge job does not install), and `TestAutoMergeDefersInsideTheSessionWindow` executes the step against `merge_deferred()` at both inclusive edges plus a fake checkout as the falsifying control. A deferred branch is re-dispatched by every watcher run and by `retry-fallback-merges.yml` (see the fifth review below). Before this, evaluation was blacked out and publication was not: a 19:50 fire merged at 20:05 would move the ledger under the 20:00 session and `assert_session_fresh` would discard the day. (2) **Stale-fill refusal** ("Reject stale watcher fallback branches"): the files the branch changed since the merge-base, minus `data/leaderboard/current.json`, are compared with the files main changed since the same base; any overlap exits 1 under the distinct title `auto-merge-session: a watcher fallback branch is stale against main`, with the paths as `details`. The session kind had "Reject stale sessions"; the watcher kind had nothing, and a branch that outlived a session could merge *clean* on top of a main where the same order had been cancelled and re-executed at market — pending file gone on both sides, inbox rows in different date files, position held twice. One reporter, two titles, the `fetch-ohlcv` pattern; a clean merge closes the "did not reach main" issue, the stale one is closed by whoever decided it. (3) **`current.json` resolves to main's copy**: both sides regenerate it, so any branch that outlives a session conflicts on it by construction; a merge whose *only* conflicting path is that file takes `--ours` (main) and completes, anything else still aborts. `TestAutoMergeResolvesLeaderboardConflicts` runs the merge step against a bare origin in both shapes. (4) **The fallback cannot self-heal under a repeat of GH006** — the merge pushes main with `GITHUB_TOKEN`, the very push a required check refuses — so its guarantee is durability plus visibility (fill on origin, issue filed and re-filed, watcher refusing), not that the fill lands. No PAT: a required check and a bot writer on main do not coexist here.

**The second review of the same work narrowed one of those, widened another, and closed two races (2026-09-05).** All four are in `tests/test_ci_guards.py`, each with a control that was made to fail.

- **The merge defers over `merge_deferred()` (SESSION_START..BLACKOUT_END), not `in_blackout()` (BLACKOUT_START..BLACKOUT_END).** The watcher's evaluation blackout opens five minutes early for its own multi-minute fire path; a merge is one fetch-merge-push. Those five minutes were not free: a fire at 19:54 pushed its fallback branch, the ~19:55 dispatch deferred, the 20:00 session appended its own fills to the same `data/orders/inbox/<date>.jsonl` and republished the books — and the stale rule then refused that branch *forever*, with every watcher run refusing to evaluate behind it. `SESSION_START` is now the single constant both windows are stated against. **The merge still has a hard deadline of that day's session**, and past it the branch is a human decision; the header and the issue say so instead of calling it self-healing.
- **Staleness is per BOOK, not per file.** A session writes `snapshots.json`, journals, posts and baselines — none of the files a fill writes — so a file-level overlap test let a branch merge *clean* behind a snapshot valued without its fill. Snapshots are immutable across sessions and `check_append_only.py` forbids correcting the row without `[restate]`, so that published valuation would have stayed wrong by the fire-to-close P&L. Everything under `data/portfolios/<agent>/` is now one conflict key; the control test proves it is still per book and not per `data/portfolios/`.
- **Every check binds the `origin/main` sha it was computed on.** The stale step exports `main_sha`; the merge step refuses any other tip and has **no retry loop**. The old `for attempt in 1 2 3` re-fetched and re-merged on each rejected push without re-running scope or staleness — and a push is rejected *only because* main moved, i.e. exactly the state nothing had checked. Reproduced: a session that cancels the branch's order merges clean (pending file deleted on both sides, the `CANCELLED_BY_AGENT` row in a different dated file), and main ends up holding a cancellation and a fill for one order. One dispatch cycle is the cost of refusing — and see the fifth review below for what makes that cycle hours rather than a day.
- **`merge-fallback-branches` runs BEFORE the watcher as well as after**, with a bounded `wait-seconds`, and the checkout is re-anchored (`fetch --depth=1` + `reset --hard`) between the two. Dispatching only afterwards meant a branch left over from a deferred or failed merge cost the *next* sweep its whole evaluation — fires and expiries — because `check_triggers.py` refuses while one exists and the merge was only asked for afterwards. A wait that times out is a `::warning::`, not a step failure. The re-anchor is required by the guard below, not cosmetic.
- **`check_triggers.py` refuses when `origin/main` is not its own `HEAD`** (`main_tip_ahead_of_checkout`, one `ls-remote`, no fetch — a `git fetch origin main` into a depth-1 checkout deepens a multi-gigabyte history). The branch guard reads origin at process start; the *worktree* is from job start and was never refreshed, and nothing serialises the watchers (`concurrency: check-triggers`) against `auto-merge-session` (per-branch group). A merge landing and deleting the branch in that gap left a clean origin and a stale worktree — pending file present, fill absent — and the run fired the order again at a new price. The rebase conflict on `portfolio.json` was the only thing keeping the duplicate off main. Fails closed on an unreadable origin, like its sibling.

**The third review closed the two ways a green run could close an issue that was still true (2026-09-05).** Both are the same family as the `gate`'s empty `EXPECTED`: a run that verified nothing reporting success.

- **A `workflow_dispatch` outside the fallback branch patterns is refused by the first step**, before anything classifies a commit. `workflow_dispatch` exists for `merge-fallback-branches` (`--ref <branch>`), but the Actions UI's "Run workflow" button defaults to the DEFAULT branch — and the issue body invites a reader to open the run. Dispatched on `main` the whole job was a green no-op that CLOSED the open issue: main's tip carries `chore(triggers): ` most days, so `inspect` called it a watcher branch; `merge-base --is-ancestor main origin/main` is trivially true, so `already_merged` skipped scope, staleness and the merge; the delete step ran `git push origin --delete main` (refused by GitHub, swallowed by `continue-on-error`); and the reporter, gated only on the kind, closed "a watcher fallback branch did not reach main" for a branch still unmerged on origin, behind which every watcher run refuses to evaluate. The guard's two patterns are asserted equal to `on: push`'s, and the refusal is placed where `steps.inspect.outputs.kind` is still empty, so a human's misfire goes red without touching the tracker.
- **A recovery is reported only when origin holds no other `triggers/*` branch** (`report_gate`). The issue is one per CAUSE, not per branch, so with two fallback branches — one merged, one refused — the green run for the second closed the issue the first had filed. A failure always reports; an origin that cannot be listed is not a recovery either, so nothing closes on an unknown. The control test proves the gate still opens on a clean origin, and that a run's own branch surviving a `continue-on-error` delete does not hold its issue open.

**The fourth review closed the last classification with no consumer (2026-09-05): `kind` is decided by the BRANCH for `triggers/*`, not by the tip's subject.** A `triggers/*` branch whose tip subject was not `chore(triggers): ` set `kind=none`, and every downstream step is gated on the kind — so the run was fully green having checked nothing, merged nothing, deleted nothing and reported nothing, while the branch stayed on origin and `check_triggers.py` refused to evaluate behind it (no fires, no expiries, desk-wide, indefinitely, with `merge-fallback-branches` re-dispatching the same green no-op every run). That tip is reachable by invitation, not by accident: the stale-refusal issue tells the reader to resolve the branch by hand and the workflow header advertises the push route, and an amend, a hand-carried fill or a revert all land a subject the regex does not match. Classified by branch, the scope step refuses the commit by name, the run goes red and the issue is filed. `claude/**` keeps `kind=none` for a non-session tip deliberately — a sandbox branch blocks nothing on origin and `session-watchdog` is its consumer. `tests/test_ci_guards.py` executes the classifier step against fixture branches for all three answers, with the `none` case as the control.

**The fifth review found the retry that did not exist (2026-09-05): `retry-fallback-merges.yml`.** Every non-fatal outcome of `auto-merge-session` — a deferral, a rejected push ("main moved after the stale check"), a merge conflict, a lost dispatch — ends with *"the next run re-dispatches this merge"*, and until this workflow the only callers of `merge-fallback-branches` were `check-triggers.yml` (`0 13 * * *`) and the crypto twin (`workflow_dispatch` only, fired by the Cloudflare gate on a level hit, i.e. on most days never). So the next attempt after the 13:00 sweep was 13:00 **tomorrow** — past the merge's hard deadline, that evening's 20:00 session — and the branch was then stale *by construction*, because the stale check keys a book as one path and every session writes `data/portfolios/<agent>/snapshots.json` for every book. One transient failure therefore reproduced the whole outage: the fill executed at the 13:00 price never published, the agent trading that evening against a book without it, and the desk halted (no fires, no expiries) from the next watcher run until a human deleted the branch and the order was re-executed by hand at a different price. The new workflow is a **dispatcher only** (`contents: read`, `actions: write`, `issues: write`) on three crons between the sweep and the session; several rather than one because the scheduler lands 40 min to 2 h 45 late with a tail past 5 h, and none close to 20:00. It is deliberately NOT extra crons on `check-triggers.yml`: that workflow *runs* the watcher, so its cadence is a money-path decision (and it is the sole owner of expiry). It reports through `failure-issue` under a title of its own — its failure means a waiting fill has nothing left that will try to merge it before the deadline, which nothing else can see. `TestAWaitingFallbackBranchIsRetriedBeforeTheSession` reads `SESSION_START` and the watcher's own cron and asserts every retry falls strictly between them.

**The failure-issue tracker has a reader outside GitHub — `~/.claude/scripts/intendant.sh`'s `midas-issues` source**, which reads the open issues live from origin and puts an automated writer's failure title in the `act` tier. It is invoked by `/brief`; whether anything *schedules* it is a question about this machine, not about this repo, so check `jobs-inventory.sh` rather than believing a line here — 17 failure issues went unread for 11 days precisely because a channel's only reader had to be summoned by hand. Two rules it learned the same day, both worth knowing before touching it: the automated-title prefixes are discovered from a LOCAL midas checkout (routinely behind origin) and are therefore **unioned with a static floor, never replaced by the discovery** — otherwise a title newer than the checkout classifies as a human-filed `warn` item, which is the misfiling the discovery exists to prevent; and `gh issue list --limit` truncates silently, so hitting the ceiling is reported as a degraded read rather than as a short queue.

**Scheduled workflows alert through `.github/actions/failure-issue`.** A red X
plus a failure email is not a closed loop — `core-drift-guard` was red three
consecutive Mondays while the public mirror shipped stale engine code. The
composite action files a GitHub issue on failure, comments on the existing one
instead of filing duplicates (idempotent per *cause*, not per date — a job
failing five days running is one fact), and closes it on the next success.
Wired into `core-drift-guard`, `fetch-ohlcv`, `fetch-sentiment`,
`refresh-universes`, `resweep-held-tickers`, **`session-integrity`**, the two
watchers, `attest-ledger`, `refresh-leaderboard`, and the watcher half of
`auto-merge-session`; `session-watchdog` keeps its own per-date variant because
each missed session is a separate fact. `tests/test_ci_guards.ALERTING_WORKFLOWS`
is the roster, not this sentence.

**`failure-issue` takes an optional `details` input** (2026-09-05, Task A3):
markdown appended to both the first issue's body AND every recurrence
comment (the comment path moved from an inline `--body` to `--body-file` for
this — a comment can now carry arbitrary markdown, matching the create path,
without a caller having to shell-escape it). Default `""` reproduces the
exact prior body/comment for every caller that does not pass it —
`tests/test_ci_guards.TestFailureIssueDetailsInput` pins the empty case
byte-for-byte, not just "still contains BODY". **Three workflows pass it**, and
`TestWatcherReportFeedsTheIssue.test_other_alerting_workflows_do_not_pass_details`
holds that set — not this sentence. `auto-merge-session.yml` is the third: its stale
refusal ships the overlapping paths as `details` (see the stale-fill rule
above), a heredoc-built bullet list rather than a table, which is the whole
actionable content of that issue — so a change to the `details` contract that
assumes a markdown table (reformatting it, truncating on a table header) guts
that alert. The two watchers pass the table: `scripts/check_triggers.py`'s
`write_run_report()` appends a markdown table of the run's fired/expired
orders (order, agent, action, ticker, trigger, observed/fill price, notional,
and where the commit ended up — `main` / a `triggers/*` branch name /
`stranded`) to **`$RUNNER_TEMP/watcher-report.md`**, the "Read watcher run
report" step right after "Run watcher" reads that file back into a
`GITHUB_OUTPUT` (`table<<EOF` — a plain `table=` assignment truncates at the
first newline), and "Report outcome" passes
`details: ${{ steps.report.outputs.table }}`. This replaced a body that told
the reader to "check whether any pending order's trigger was hit" with the
exact numbers. **Not `$GITHUB_STEP_SUMMARY`, which is per-step** — GitHub:
"unique to the current step and changes for each step in a job". The first
version read it from the next step, got its own fresh empty file, and shipped
`details` empty on every run while nothing went red; the only test grepped the
step for the env var's name. `TestWatcherReportFeedsTheIssue` now runs the
writer and the reader with two different summary files and one `$RUNNER_TEMP`,
the runner's real shape, and was made to fail against the old reader first.
The table is still appended to the watcher step's own `$GITHUB_STEP_SUMMARY`
for the summary tab — display, no reader. The JSON twin goes to
`$WATCHER_REPORT_PATH` (default `$RUNNER_TEMP/watcher-report.json`; skipped
with neither env var set, which is every local run and every test) and its
consumer is the `actions/upload-artifact@v4` step in both workflows —
`watcher-report-<run id>`, `if: always()`, `if-no-files-found: ignore` because
a blacked-out or refused run writes nothing. Before that step it was written
and discarded with the runner on 100% of runs.

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

## Moved out of CLAUDE.md on 2026-09-05

Verbatim. Nothing was cut in the move; the guards stayed behind.

**Hypothesis settings live in one profile** (`tests/conftest.py`, `midas`):
`max_examples=1000` per the portfolio mandate, and `deadline=None`. The deadline
is wall-clock and several properties touch the filesystem, so under CI load it
produced intermittent `DeadlineExceeded` failures — a red suite caused by a busy
machine, which trains people to re-run rather than read. Runtime is bounded by
`max_examples` instead, a property of the test rather than of the machine.
Per-test `@settings` had already drifted to 200/300/400 before this; write none
and inherit the profile. `tests/test_money_properties.py` covers the four
transforms a euro actually travels through: unit normalisation, FX conversion,
order serde, fees.

**Published data is guarded in CI, not only in application code** (2026-08-07,
review W4). `session-integrity.yml` now runs three data checks on every push to
main, none of which existed as a standing gate before:
- **ledger-integrity** — every filled inbox row has a matching trade (existence).
- **ledger cash-replay** (`tests/test_ledger_cash.py`) — `initial_capital +
  replay(trades) == live cash`, per book. The existence check cannot see a trade
  booked at the *wrong notional*, which is what the quote-currency defect did to
  24 fills: every row present, joined cleanly, €2,057.65 wrong. The arithmetic is
  imported from `scripts/restate_valuations.py`, not reimplemented.
- **baseline freshness** (`scripts/check_session_freshness.py`, 2026-08-07) —
  the `check` job's Step 9 assertion. It used to grep the commit's changed-file
  list for `^data/baselines/`, which is a proxy for the thing it cares about,
  and the two came apart the same day the append-or-refuse contract landed:
  `a4dc9dce2 [restate]` rebuilt every series that morning, so the evening
  session's Step 9 ran against an already-current series, `merge_baseline_series`
  correctly wrote nothing, and both this guard and the inline copy in
  `auto-merge-session` failed a correct session. **The auto-merge one gates the
  merge** — it was inert only because the direct push to main had already
  succeeded. A diff cannot answer "did Step 9 run"; the published state can: a
  genuine skip leaves the baselines *behind* the snapshots (the Apr 25 shape),
  a correct no-op leaves them level, and a restatement may legitimately run
  ahead, so the check is one-sided. Stdlib only, so no runner needs
  `setup-python`. Calibrated by replaying the real `check` step against
  `32038bcf8` — the commit that went red — and against a hand-broken copy of it.
- **append-only** (`scripts/check_append_only.py`) — a dated row in
  `data/portfolios/*/snapshots.json` or `data/baselines/**` that already exists
  at `HEAD^` must be byte-identical at `HEAD`. A session correcting **its own**
  row is allowed (same `session_date`, exactly what `add_snapshot` permits);
  baselines get no such exemption because they have no writer identity. Anything
  else needs `[restate]` in the commit message — the disclosure requirement made
  mechanical, so `git log --grep='\[restate\]'` is a complete list of every time
  the published record moved. Deliberately a post-hoc detector, not a merge gate:
  `auto-merge-session.yml` runs its own inline copy of the artifact rules, so
  this cannot hold a session hostage. Calibrated by replaying it over real
  history, not only fixtures — the gate's ability to fire is pinned against
  seven real mutating commits BY SHA (`KNOWN_FIRING_COMMITS`), because the
  40-commit scan that used to carry that proof drifted past every one of them
  by 2026-08-11 and asserted nothing. The scan survives as the separate
  "no new mutation route opened" check, where finding nothing is the pass.
  Neither runs in CI — `fetch-depth: 1`, and `.git` is 2.5 GB.

**The daily attestation asserts something now.** `attest-ledger.yml` ran for 55
green days computing a digest and checking nothing about it — a tampered ledger
produced a different hash and a green run. `attest_ledger.py --verify` re-derives
the previous `attest/*` tag's digest from that tag's own tree (via a detached
worktree, so files deleted since are still covered) and fails on divergence.
