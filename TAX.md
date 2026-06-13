# Tax context — French fiscal residency

Context for real-money operation of the Midas agents. All figures current as of April 2026 and apply to an individual French tax resident. Not legal advice — verify with an accountant before deploying capital.

## 1. PFU (Prélèvement Forfaitaire Unique) — 30% flat tax

Since 2018, investment income and capital gains for French tax residents are subject to a unified flat tax of **30%**, broken down as:
- **12.8%** income tax (impôt sur le revenu)
- **17.2%** social charges (CSG, CRDS, prélèvement de solidarité)

Applies to:
- Capital gains on securities (stocks, ETFs, bonds) — *plus-values sur valeurs mobilières*
- Capital gains on crypto — *plus-values sur actifs numériques* (Article 150 VH bis CGI)
- Forex and derivatives trading gains
- Dividends and interest

Opt-out: taxpayers may elect to have these incomes taxed at the progressive income-tax scale instead (barème progressif). Beneficial only for low marginal brackets. Default and usually better option: PFU.

Capital **losses** offset gains within the same asset class in the current year; unused losses carry forward 10 years for securities, and can be carried forward for crypto as well (same year only, no long carry-forward for crypto per current rules).

## 2. Foreign account declaration — form 3916 / 3916-bis

Every account held abroad must be declared annually on the French income-tax return (déclaration 2042), regardless of activity or balance.

- **Form 3916**: securities and cash accounts with foreign brokers (IBKR Ireland, OANDA Europe, etc.). One form per account.
- **Form 3916-bis**: digital-asset accounts with foreign exchanges (Kraken, Binance, Bitpanda, etc.). One form per account.

**Penalties for non-declaration**: €1,500 per account per year (up to €10,000 per account if the country is not in a tax information exchange agreement with France — rare for OECD brokers).

**Applies even in years with no activity.** Once declared, keep declaring until closure.

## 3. Broker-specific implications

| Agent | Broker | Declaration form | Notes |
|-------|--------|------------------|-------|
| goldfinger | Interactive Brokers Ireland (IBIE) | Form 3916 | One form. IBIE does **not** issue an IFU (imprimé fiscal unique) and does not transmit trade-level data to the DGFiP. IBIE provides an annual **Activity Statement**; the taxpayer self-computes gains from it and self-declares on form **2074-CMV** (net carried to form 2042). IBIE reports only aggregate balances/proceeds to France via CRS — not trade-level detail. |
| monsieur-forex | OANDA Europe (Ireland) | Form 3916 | Forex realized gains are calculated per closed position. OANDA issues an annual statement. |
| satoshi | Kraken | Form 3916-bis | Every BUY→SELL is a taxable event. Kraken provides a CSV export; consider tools like Koinly, Waltio, or CoinTracking for French Cerfa-ready reports. |
| sharp-shooter | Interactive Brokers Ireland (IBIE) | Form 3916 | Same account as goldfinger potentially — or separate sub-accounts. One form per distinct account. |
| steady-eddie | Interactive Brokers Ireland (IBIE) | Form 3916 | Same. |
| yolo-sapiens | IBIE + Kraken | Forms 3916 AND 3916-bis | Two forms (one per account). |

In this project, each agent is a logical portfolio. In real deployment, you'd likely consolidate into **one IBIE account** (all equity/ETF/forex agents) plus **one Kraken account** (satoshi + yolo's crypto sleeve) — two declarations total, not six.

## 4. PEA — why it doesn't apply

The **Plan d'Épargne en Actions** is the most tax-efficient French equity vehicle (only 17.2% social charges after 5 years, zero income tax). However it is restricted to **EU-domiciled equities and EU-listed UCITS ETFs**.

The Midas agents trade US stocks (Steady Eddie, Sharp Shooter), US ETFs (Goldfinger's GLD/SLV/USO), crypto (Satoshi), and FX (Monsieur Forex) — none of which are PEA-eligible. So the agents run in a standard CTO (compte-titres ordinaire) via IBIE, subject to full PFU 30%.

If Steady Eddie's mandate were ever retargeted to an EU equity universe (CAC 40 / STOXX 600), a PEA wrapper could meaningfully improve his after-tax return.

## 5. Operational consequences for the agents

- **Fee drag is real**, but **tax drag is bigger**: 30% of every gain. Agents with high turnover (Satoshi, Sharp Shooter) pay more in taxes than fees. Long-holding agents (Steady Eddie, Goldfinger) pay taxes only on realization.
- **No tax-deferred compounding** as you'd get in a US IRA or French PEA — every realized gain is taxed the year it occurs.
- **Offset losses deliberately**: at year-end, consider crystallizing losses to offset the year's gains. This is a real-money decision, not a backtest one.
- **Crypto FIFO vs portfolio method**: French crypto tax uses a global-portfolio method — every disposal's cost basis is a weighted average of your total crypto holdings at the time, not the specific lot sold. Satoshi's tax exposure depends on the WHOLE crypto portfolio, not just the position closed.

## 6. References (to verify before acting)

- impots.gouv.fr — *Imposition des revenus de capitaux mobiliers*
- BOFIP BOI-RPPM-PVBMI — plus-values de cessions de valeurs mobilières
- BOFIP BOI-RPPM-PVBMC-30-10 — plus-values sur actifs numériques
- impots.gouv.fr — formulaires 3916 et 3916-bis
- ACPR registry — list of PSAN-registered crypto providers (Kraken, Binance, Coinbase, etc.)

---

*This file is a working reference, not tax advice. Consult a licensed accountant before deploying real capital. Tax rules change — revisit annually.*
