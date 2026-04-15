# The Midas Experiment

**Can an AI beat the market?**

Six Claude agents. $10,000 each. One month. No human intervention. Let's find out.

---

## The Setup

I gave Claude Code — Anthropic's AI coding assistant — a simple challenge: *manage my money*. Not as a tool I control, but as an autonomous fund manager that researches markets, picks stocks, and makes trades on its own.

To make it interesting, I created six competing agents, each with a different personality, asset class, and risk tolerance. They all start with $10,000 in paper money. They run daily. They don't know what each other is doing. At the end of the month, we'll see who wins — and whether any of them can beat simply buying the S&P 500 and doing nothing.

### The Agents

| Agent | Style | Universe | Risk | Personality |
|-------|-------|----------|------|-------------|
| **Steady Eddie** | Conservative value | US large-cap stocks | Low | The pension fund manager. Thinks in quarters, not days. Sleeps well at night. |
| **Sharp Shooter** | Aggressive momentum | Stocks + leveraged ETFs | High | The hedge fund trader. Rides trends, cuts losers fast. Doesn't apologize. |
| **Satoshi** | Crypto specialist | Top 20 cryptocurrencies | Very high | The on-chain analyst. Reads exchange flows, halving cycles, and regulatory tea leaves. |
| **Monsieur Forex** | Forex macro | Major currency pairs | Medium | The central banker whisperer. Trades rate differentials and policy divergence. |
| **Goldfinger** | Commodities | Gold, silver, oil | Medium | The inflation hedger. Watches real yields, central bank reserves, and geopolitical risk. |
| **YOLO Sapiens** | Cross-asset wildcard | Anything goes | Maximum | The degenerate. 3x leveraged ETFs, crypto, whatever has the most asymmetric upside. Goal: double in 6 months. |

### The Baselines

To know if the agents are any good, we compare them against strategies that require zero intelligence:

- **VOO Buy & Hold** — buy the S&P 500 index, do nothing. The benchmark every fund manager fears.
- **Equal Weight Hold** — buy everything equally, hold forever. Tests whether picking adds value.
- **60/40 Classic** — 60% stocks, 40% bonds. The financial advisor default.
- **Coin Flip** — pick random stocks. The null hypothesis. If you can't beat random, you have no edge.

### The Rules

- $10,000 starting capital per agent, per strategy
- Trades execute at closing prices (agents run after market close)
- $0 commission (matches modern brokers)
- No position can exceed portfolio limits set per agent
- Every trade must include written reasoning — no black-box decisions
- Agents cannot see each other's portfolios

---

## Day 1 — April 14, 2026

### The Market

| Index | Value |
|-------|-------|
| S&P 500 | 6,967.38 |
| Gold | $4,825.00 |
| Bitcoin | $74,181.61 |
| MSCI World | 192.00 |

The backdrop: a jittery market. The S&P has formed a death cross (50-day MA below 200-day), which sounds scary but historically has a mixed predictive record. Energy is the clear winner of 2026 so far (+34% YTD), while tech — the market's darling for years — is nursing wounds (Microsoft down 23% YTD). Gold is at an eye-watering $4,825 on central bank buying and Strait of Hormuz tensions. Bitcoin is consolidating near $74K in what on-chain data suggests is an accumulation zone.

In other words: everything is happening at once.

### Opening Positions

**Steady Eddie** deployed $8,037 across 7 positions, keeping 20% cash.

Eddie did something interesting on day one: he bought Microsoft. While every headline screams "tech is dead," Eddie noticed MSFT is trading at a P/E of 24.5 — well below its 5-year median of 33. That's a 23% discount on arguably the highest-quality tech company in the world. Classic value move: buy what everyone else is selling, but only when the fundamentals are intact. His other picks — JNJ, JPM, PEP, XOM, MDT, HD — read like a "greatest hits of boring but profitable companies" playlist.

**Sharp Shooter** went all-in at $8,136 across 7 positions.

Sharp Shooter is betting on a tech comeback — but with leverage. 12 shares of TQQQ (3x Nasdaq) is the core position. If he's right about tech bottoming, the 3x leverage turns a 10% bounce into 30%. If he's wrong, it amplifies the pain equally. He also grabbed gold (momentum play, not a hedge) and crypto exposure through MSTR and COIN. This portfolio will either be the hero or the cautionary tale.

**Satoshi** deployed $9,021, keeping just $979 in reserve.

The most research-intensive agent. Satoshi noted that the Crypto Fear & Greed Index has been at 8 (extreme fear) for 60+ consecutive days — historically, that's a 78% probability of positive returns over the next 14 days. He also cited BlackRock's IBIT hitting $54 billion in AUM and the SEC's March 17 commodity classification of 16 cryptos as structural catalysts. His portfolio is BTC-heavy (core position) with ETH, SOL (citing the Firedancer upgrade), and a tail of LINK, XRP, and AVAX. The cash reserve? "For any tax-season dip tomorrow" — April 15 is US tax day.

**Monsieur Forex** deployed $8,000 across 6 currency pairs.

The most macro-driven agent. His thesis is simple: the US dollar is weak and getting weaker. DXY at multi-year lows, Fed dovish while everyone else tightens. He laid out rate differentials for six central banks (Fed 3.50%, ECB 2.00%, BOJ 0.75%, BOE 3.75%, RBA 4.10%, RBNZ 2.25%) and positioned accordingly — long EUR, AUD, GBP, NZD against the dollar. The cleanest trade, per Monsieur: long yen via USD/JPY, because the BOJ is hiking to 1% while the Fed cuts. He also noted that Iran ceasefire negotiations collapsed over the weekend — the kind of geopolitical detail most stock-focused agents miss entirely.

**Goldfinger** deployed only $6,907 — the smallest deployment, with 31% cash.

The most disciplined agent. While everyone else rushed to fill their portfolios, Goldfinger looked at gold near all-time highs and said: "I'll buy, but I'm keeping a third of my capital for when it pulls back." His GLD position is the core (49% of deployed capital). Silver gets a smaller allocation because "it's extended after a 130% rally in 2025." Oil (USO) gets the smallest position because "geopolitical premiums are inherently fragile." This is the agent most likely to survive a crash — and most likely to underperform in a continued rally.

**YOLO Sapiens** deployed $9,844 — leaving $156 in cash. Enough for a coffee.

The portfolio is a lever on a lever on a lever. TQQQ (3x Nasdaq) and SOXL (3x semiconductors) make up the core — these are instruments that go up 3% when their index goes up 1%, and down 3% when it goes down 1%. Add NUGT (2x gold miners), MSTR (leveraged Bitcoin proxy), and direct BTC exposure. The thesis, as YOLO stated it: "If Nasdaq moves 30%, these return 90%." True. Also true in the other direction. This portfolio will be the most volatile by far. It's not investing — it's a calculated bet that the current trends continue. As YOLO put it: "Cash doesn't double."

### The Scoreboard After Day 1

Everyone starts at $10,000. No gains or losses yet — we'll measure from tomorrow's close. But the table is set:

| Agent | Deployed | Cash | Positions | Bet |
|-------|----------|------|-----------|-----|
| Steady Eddie | $8,037 | $1,963 | 7 | Quality value in a volatile market |
| Sharp Shooter | $8,136 | $1,864 | 7 | Tech comeback via leverage |
| Satoshi | $9,021 | $979 | 6 | Crypto fear = opportunity |
| Monsieur Forex | $8,000 | $2,000 | 6 | USD weakness across the board |
| Goldfinger | $6,907 | $3,093 | 3 | Gold bull but cautious on timing |
| YOLO Sapiens | $9,844 | $156 | 5 | Leverage everything, pray |

### Day 1 Observations

Three things stand out:

1. **Every agent bought gold in some form.** Eddie didn't, but Sharp Shooter bought GLD, Goldfinger is all-in on metals, YOLO has NUGT (2x gold miners). When six independent AI agents all see the same signal, that's worth noting — even if it also means the trade is crowded.

2. **The energy divergence.** Sonnet (the previous, weaker model) went all-in on energy — ERX, XOM, CVX. Opus (the current, stronger model) barely touched it. Eddie has XOM but as one of seven positions. Sharp Shooter skipped energy entirely for tech and crypto. The smarter model saw further ahead: energy has already had its run (+34% YTD), and the question now is continuation vs. rotation.

3. **Cash management reveals personality.** YOLO: $156 left. Goldfinger: $3,093. That spread — from "cash doesn't double" to "everything is near highs, let's wait" — is the single best illustration of what different risk mandates look like in practice. Both are valid. Neither is wrong. The market will decide which was wiser.

Tomorrow we'll see the first P&L. The experiment begins.

---

*This is a live experiment. Updated daily. No trades are made with real money (yet). Built with [Claude Code](https://claude.ai/code) by [William](https://william.revah.paris).*
