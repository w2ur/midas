# Disclaimer

**This repository is a public record of a paper-trading experiment. It is not
financial advice, not a service, and not an offer of anything.**

`midas-core` ships its own [disclaimer](https://github.com/w2ur/midas-core/blob/main/DISCLAIMER.md)
covering the framework. This one covers *this* repo, which is different in one
way that matters: the framework's demo desk is a fixture, whereas the numbers
here are a real, dated, append-only record of simulated decisions. That makes
them look more like a track record than they are.

- **No money is at risk and none ever has been.** Every fill in
  `data/orders/inbox/` was produced by `engine/paper_broker.py` against a
  committed price store. There is no broker connection, no credential, and no
  account. Nothing here manages anyone's funds, including the author's.
- **Not financial advice.** The agents' commentary, journals, posts and the
  Oracle's column are the output of language models writing in character. They
  are narrative artifacts. Nothing in them is a recommendation to buy, sell or
  hold anything, and none of it accounts for your circumstances.
- **Simulated results are not evidence of skill.** The books are a small
  sample over a short window, and the experiment's own methodology puts the
  noise floor at **±6 percentage points** — wide enough to swallow most of the
  differences you will see on the leaderboard. Rankings move on FX translation
  and universe composition as much as on decisions. Read
  [METHODOLOGY.md](./METHODOLOGY.md) before drawing a conclusion from any
  figure, and treat the leaderboard as a story device rather than a result.
- **The simulation is not the market.** Fills are modelled at the end-of-day
  close with a per-asset-class fee model. There is no slippage, no partial
  fill, no borrow, no market impact, and no dividend cash. A strategy that
  works here has not been shown to work anywhere else.
- **The record moves, and says so when it does.** Published figures have been
  restated when a defect was found. Every restatement is written up in the
  changelog in [METHODOLOGY.md](./METHODOLOGY.md); since 2026-08-07 each also
  carries a `[restate]` marker in its commit message, so
  `git log --grep='\[restate\]'` is a complete list from that date onward but
  not before it — the largest restatement in the record predates the
  convention. Do not assume a number you read once is the number that stands
  today.
- **No warranty.** The source code is MIT (see [LICENSE](./LICENSE) and
  [NOTICE.md](./NOTICE.md) for what the licence does and does not cover).
  Provided "as is", without warranty of any kind. You are solely responsible
  for anything you build on it, including any real-money system.
