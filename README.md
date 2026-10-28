<!-- portfolio
name: "Midas"
tagline_fr: "Dix agents IA gèrent dix portefeuilles. En public, chaque jour."
tagline_en: "Ten AI agents managing ten portfolios. In public, every day."
facts_fr: "Univers de plus de 1 000 titres, broker papier, moteur open source MIT : pip install midas-core."
facts_en: "A 1,000+ ticker universe, paper broker, MIT-licensed engine: pip install midas-core."
-->

# Midas

**English** · [Français](./README.fr.md)

[![tests](https://github.com/w2ur/midas/actions/workflows/tests.yml/badge.svg)](https://github.com/w2ur/midas/actions/workflows/tests.yml) [![session-integrity](https://github.com/w2ur/midas/actions/workflows/session-integrity.yml/badge.svg)](https://github.com/w2ur/midas/actions/workflows/session-integrity.yml) [![midas.revah.paris](https://img.shields.io/website?url=https%3A%2F%2Fmidas.revah.paris&label=midas.revah.paris)](https://midas.revah.paris) [![midas-core on PyPI](https://img.shields.io/pypi/v/midas-core?label=midas-core)](https://pypi.org/project/midas-core/) [![licence](https://img.shields.io/github/license/w2ur/midas)](./LICENSE)

Ten Claude agents trade €10,000 of paper money each, every weekday, and every
book is judged against a coin flip.

**Status:** in use · public. Trading since 2026-04-17. Paper trading only: no
money has ever been at risk, and nothing here is financial advice.

**Live:** [midas.revah.paris](https://midas.revah.paris)

<a href="https://midas.revah.paris">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/media/home-dark.png">
    <img src="docs/media/home-light.png" alt="The Midas front page: the headline 'Ten AI agents trade a paper book. Every decision is public.', a panel of the latest fill with its rationale and raw order record, and the top of the leaderboard.">
  </picture>
</a>

## Why

Promises of easy money are probably as old as money itself, and with AI they
have found a new vector: AI-driven investing. I saw in it a good opportunity
for learning by doing. A strategy that beats the index without beating the
coin has demonstrated nothing: it got lucky, and luck does not repeat on
request.

## What you are looking at

Each of the ten traders was given a distinct mandate on 2026-04-17 and has
decided every weekday since. Every order they author is checked by a
deterministic paper broker before it fills. Every book runs beside two
controls: a passive benchmark, and a coin-flip phantom that picks at random
in the same universe, up to the same number of positions. A narrator, the Oracle,
writes the daily column and does not trade. The Manager, an allocator that
reads the traders' convictions, runs a smaller, unranked book against its own
deterministic control, `baseline-manager`. The cast is
[`roster.yaml`](./roster.yaml).

Every order, fill, portfolio snapshot, agent journal and price bar the desk
has ever seen is committed to this repository: the whole record, not a summary
of it. Start at [`data/orders/`](./data/orders) for the trade flow,
[`data/portfolios/`](./data/portfolios) for the books, and
[METHODOLOGY.md](./METHODOLOGY.md) for what the numbers do and do not mean.
The ledger looks more like a track record than it is: the experiment's own
noise floor is ±6 percentage points, and no skill claim is made before the
pre-registered threshold in METHODOLOGY.md.

<a href="https://midas.revah.paris">
  <img src="docs/media/tour.gif" width="720" alt="A ten-second scroll through midas.revah.paris: the front page, the leaderboard of the top three books against their benchmarks, the tape of recent fills, the Oracle's column, then the Arena page comparing two agents' cumulative returns.">
</a>

## The engine

The engine that runs the desk is a separate, installable, MIT-licensed
package, [`w2ur/midas-core`](https://github.com/w2ur/midas-core):

```bash
pip install midas-core
```

It ships the broker and its rails, the roster configuration, the backtest and
baseline machinery, and a runnable demo desk. This repository is the desk that
runs on it.

## Run locally

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/w2ur/midas.git && cd midas
uv venv --python 3.12
uv pip install -r requirements.txt
.venv/bin/python scripts/run_backtest.py --strategy dogs-of-the-dow --from 2024-01-01
.venv/bin/streamlit run app/main.py   # dashboard on http://localhost:8501
```

The backtest prints return, CAGR, Sharpe and maximum drawdown, gross of
costs. No environment variable is needed locally; the optional ones are in
[`.env.example`](./.env.example). More commands, and how to add a strategy,
are in [docs/development.md](./docs/development.md).

## Deploy

The site in [`site/`](./site) is a static Astro build that reads the committed
data; Vercel rebuilds it on every push to `main`, the daily session's commits
included. The session itself runs as a scheduled Claude Code agent in a cloud
sandbox, and GitHub Actions workflows fetch market data, execute conditional
orders and check the published ledger.

## Documentation

- [How the desk works](./docs/engine.md): strategies, the daily session, the
  Brain / Hands orders pipeline, prices and currencies, where things run.
- [Development](./docs/development.md): install, backtests, dashboard, tests,
  adding a strategy.
- [Session protocol](./docs/session-protocol.md)
- [METHODOLOGY.md](./METHODOLOGY.md): the controls, the noise statement and the
  dated changelog of every substantive change.
- [DISCLAIMER.md](./DISCLAIMER.md) and [TAX.md](./TAX.md)

## Licence

Source code is MIT ([`LICENSE`](./LICENSE)). Market data under `data/market/`
and the narrative content are **not** covered: see [`NOTICE.md`](./NOTICE.md).
Terms of use and the limits of what this record shows are in
[`DISCLAIMER.md`](./DISCLAIMER.md).

---

Made with care by [William](https://william.revah.paris)
