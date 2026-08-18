// Scheduled handler: decide, hourly, whether check-triggers-crypto.yml is worth
// a GitHub Actions run.
//
// Why this exists at all: the workflow's own hourly cron billed ~2 minutes a
// run (23 runs/day, jobs measured 58-71s astride GitHub's round-up-to-the-
// minute edge) to do ~5 seconds of work, projecting ~1,150-1,200 of the
// account's 2,000 monthly minutes. The reading and the comparison are cheap;
// only the ACT of firing needs a runner. So the comparison moved here and the
// runner is started only on a hit.
//
// What this is NOT: an order processor. It retires nothing, fills nothing and
// applies no rail. The dispatched workflow runs the real check_triggers.py with
// every rail and its own idempotency. If this Worker dies, crypto conditionals
// degrade to the daily 13:00 UTC sweep — which evaluates every asset class and
// is the sole owner of expiry. That degradation is safe, which is why an
// isolate-level death (where no handler code runs) is an accepted risk.

import { gateable, shouldDispatch } from "./gate.js";

const OWNER = "w2ur";
const REPO_NAME = "midas";
const REPO = `${OWNER}/${REPO_NAME}`;
const WORKFLOW_FILE = "check-triggers-crypto.yml";
const ISSUE_TITLE = "trigger-gate worker failing";

// Both pending channels: the public one and the allocator's. Kept in step with
// roster.yaml's allocator channels_prefix by tests/test_trigger_gate_parity.py
// — a channel missing here is a silent under-dispatch, which looks exactly like
// a quiet market.
const PENDING_PATHS = ["data/orders/pending", "data/orders/manager-pending"];

async function gh(env, path, init = {}) {
  const res = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_PAT}`,
      // GitHub rejects requests with no User-Agent.
      "User-Agent": "midas-trigger-gate",
      Accept: "application/vnd.github+json",
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`GitHub ${path} -> ${res.status}: ${body.slice(0, 300)}`);
  }
  return res;
}

/**
 * Every pending order across both channels, in ONE request.
 *
 * The REST contents API would need one call per file (77 pending today) and
 * Cloudflare's free plan allows 50 subrequests per invocation, so the obvious
 * implementation breaks at exactly the volume this desk already runs.
 */
export async function pendingOrders(env) {
  const query = `query($owner:String!,$name:String!,$pub:String!,$mgr:String!){
    repository(owner:$owner,name:$name){
      pub: object(expression:$pub){ ...files }
      mgr: object(expression:$mgr){ ...files }
    }
  }
  fragment files on GitObject { ... on Tree { entries { name object { ... on Blob { text } } } } }`;
  const res = await gh(env, "/graphql", {
    method: "POST",
    body: JSON.stringify({
      query,
      variables: {
        owner: OWNER,
        name: REPO_NAME,
        pub: `main:${PENDING_PATHS[0]}`,
        mgr: `main:${PENDING_PATHS[1]}`,
      },
    }),
  });
  const body = await res.json();
  // GraphQL answers 200 with an errors array; without this check a broken query
  // reads as "no pending orders", which is indistinguishable from a quiet desk.
  if (body.errors) {
    throw new Error(`GraphQL: ${JSON.stringify(body.errors).slice(0, 300)}`);
  }
  const repository = body?.data?.repository;
  if (!repository) throw new Error("GraphQL: no repository in response");

  const orders = [];
  for (const dir of [repository.pub, repository.mgr]) {
    // A null dir is legitimate: an allocator channel may hold no pending file.
    for (const entry of dir?.entries ?? []) {
      if (!entry.name.endsWith(".json") || !entry.object?.text) continue;
      try {
        orders.push(JSON.parse(entry.object.text));
      } catch {
        // Unparseable: list_pending() logs and skips it too. The daily sweep owns it.
      }
    }
  }
  return orders;
}

/** Coinbase spot, e.g. BTC-EUR. Public endpoint, no key. */
export async function spotPrice(pair) {
  const res = await fetch(`https://api.coinbase.com/v2/prices/${pair}/spot`, {
    headers: { "User-Agent": "midas-trigger-gate" },
  });
  if (!res.ok) throw new Error(`coinbase ${pair} -> ${res.status}`);
  const amount = parseFloat((await res.json())?.data?.amount);
  if (!Number.isFinite(amount)) throw new Error(`coinbase ${pair} -> unparseable amount`);
  return amount;
}

/**
 * Self-report. Cloudflare has no native alerting for a failing cron on the free
 * plan, and this Worker has no red X anywhere a human looks — so it files the
 * issue itself, find-or-comment like .github/actions/failure-issue: one issue
 * per cause, not one per occurrence.
 *
 * Known dead-man: if the PAT itself is what died, this POST uses the same dead
 * credential and cannot report. The degradation (daily sweep) is safe but
 * silent, which is why the PAT's renewal date belongs in a calendar.
 */
/**
 * The open issue with our exact title, or null.
 *
 * Listed and exact-matched rather than searched: `/search/issues` is a
 * relevance query that will happily rank a different issue first, which is the
 * same reason .github/actions/failure-issue does an exact-title match instead
 * of `gh issue list --search`.
 */
async function openFailureIssue(env) {
  const issues = await (
    await gh(env, `/repos/${REPO}/issues?state=open&per_page=100`)
  ).json();
  return issues.find((i) => i.title === ISSUE_TITLE && !i.pull_request) ?? null;
}

async function fileFailureIssue(env, err) {
  const existing = await openFailureIssue(env);
  const body = `Cron invocation failed at ${new Date().toISOString()}:\n\n\`\`\`\n${String(
    err,
  ).slice(0, 1500)}\n\`\`\`\n\nCrypto conditional orders are degraded to the daily 13:00 UTC \`check-triggers\` sweep until this is fixed. That sweep evaluates every asset class and owns expiry, so nothing is lost — only intraday granularity.`;
  if (existing) {
    await gh(env, `/repos/${REPO}/issues/${existing.number}/comments`, {
      method: "POST",
      body: JSON.stringify({ body }),
    });
  } else {
    await gh(env, `/repos/${REPO}/issues`, {
      method: "POST",
      body: JSON.stringify({ title: ISSUE_TITLE, body, labels: ["ci"] }),
    });
  }
}

/**
 * Close the failure issue once an invocation completes cleanly.
 *
 * .github/actions/failure-issue closes on the next success for a stated
 * reason — "an issue tracker nobody trusts to be current is the same dead end
 * as an unread email" — and this mirror was file-or-comment only, so a fixed
 * cause left an open issue and the next real failure read as a continuation
 * of a stale one. Best-effort: never let the bookkeeping fail a good run.
 */
async function closeFailureIssue(env) {
  const existing = await openFailureIssue(env);
  if (!existing) return;
  await gh(env, `/repos/${REPO}/issues/${existing.number}/comments`, {
    method: "POST",
    body: JSON.stringify({
      body: `Recovered at ${new Date().toISOString()} — a cron invocation completed cleanly. Closing.`,
    }),
  });
  await gh(env, `/repos/${REPO}/issues/${existing.number}`, {
    method: "PATCH",
    body: JSON.stringify({ state: "closed" }),
  });
}

export default {
  async scheduled(event, env, ctx) {
    try {
      const today = new Date().toISOString().slice(0, 10);
      const candidates = gateable(await pendingOrders(env), today);
      if (candidates.length === 0) {
        console.log("no live crypto pending orders — no dispatch");
        await closeFailureIssue(env).catch(() => {});
        return;
      }

      // Price each pair in ISOLATION. A throw here used to abort the whole
      // invocation, so one pair Coinbase does not serve suppressed the gate for
      // every other order until that order expired — a systematic
      // under-dispatch, the one error direction this gate must never make.
      // engine.triggers.get_current_price does the same thing: it catches
      // everything and returns None for that ticker alone.
      const pairs = [...new Set(candidates.map((o) => o.ticker))];
      const prices = {};
      const priceFailures = [];
      for (const pair of pairs) {
        try {
          prices[pair] = await spotPrice(pair);
        } catch (err) {
          // A permanently dead symbol is normal (MATIC-USD has served nothing
          // since 2025-03-24) and must not page anyone hourly. Only a TOTAL
          // failure is systemic, and that is escalated below.
          priceFailures.push(`${pair}: ${String(err).slice(0, 120)}`);
        }
      }
      if (priceFailures.length > 0) {
        console.log(`could not price ${priceFailures.length}/${pairs.length}: ${priceFailures.join("; ")}`);
      }
      if (priceFailures.length === pairs.length) {
        // Every pair failed: that is the exchange or the network, not a dead
        // symbol, and it means the gate priced nothing at all.
        throw new Error(`no pair could be priced (${pairs.length}): ${priceFailures.join("; ")}`);
      }

      // A pair that did not price is `undefined` here, which shouldDispatch
      // rejects on its Number.isFinite check — carried to the daily sweep.
      const hits = candidates.filter((o) => shouldDispatch(o, prices[o.ticker]));
      if (hits.length === 0) {
        console.log(
          `${candidates.length} live crypto order(s), none at level — no dispatch`,
        );
        await closeFailureIssue(env).catch(() => {});
        return;
      }

      await gh(env, `/repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`, {
        method: "POST",
        body: JSON.stringify({ ref: "main" }),
      });
      console.log(
        `dispatched ${WORKFLOW_FILE}: ${hits.map((o) => o.order_id).join(", ")}`,
      );
      await closeFailureIssue(env).catch(() => {});
    } catch (err) {
      // Report first, then rethrow so the invocation also shows as failed in
      // Cloudflare's cron event history.
      await fileFailureIssue(env, err).catch((e) =>
        console.error("could not file the failure issue:", String(e)),
      );
      throw err;
    }
  },
};
