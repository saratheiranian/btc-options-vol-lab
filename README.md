# BTC Options Volatility Lab

[![Tests](https://github.com/saratheiranian/btc-options-vol-lab/actions/workflows/tests.yml/badge.svg)](https://github.com/saratheiranian/btc-options-vol-lab/actions/workflows/tests.yml)

A Black-Scholes pricing engine, tested against Deribit's live Bitcoin options market, and used to show **where the model breaks**: the volatility smile it can't explain, and the risk left over when you try to hedge the way it says you can.

Three parts:

1. **Pricing engine.** Black-Scholes-Merton and Black-76 prices, all five Greeks, and an implied-volatility solver (Newton-Raphson with a bisection fallback), checked against a textbook example and finite differences.
2. **Live volatility smile.** Every BTC option on Deribit, priced by this engine. Its implied volatilities are checked against Deribit's published figures, then used to map the smile, skew, and term structure.
3. **Delta-hedging lab.** What a hedger actually loses when rebalancing is discrete, volatility is misjudged, trading costs money, and prices jump the way real Bitcoin does.

---


## How it works
## Results

Live snapshot of Deribit's BTC options market, 28 September 2026, 21:03 UTC.

| | |
|---|---|
| Options priced | 888 options across 11 expiries |
| Engine vs Deribit's published mark IV | median gap 0.012 vol points; 93.8% within 0.5 |
| ~30-day ATM implied vol / skew (30 Oct 2026 expiry) | 34.9% / +1.1 vol points |
| Term structure | ATM vol rises from 28.0% (1.5 days) to 39.2% (1 year) |
| Short-dated skew | +9.7 vol points at 1.5 days, fading to about +0.5 beyond 3 months |
| Hedging error, daily vs hourly rebalancing | 4.93× (theory √24 ≈ 4.90×) |
| Hourly hedging on real BTC prices vs the model (std. dev. of P&L) | $836 vs $96, about 8.7× riskier; worst 5%: −$1,633 vs −$159 |

Hedging on real prices is far riskier than Black-Scholes predicts: real Bitcoin jumps, and its volatility changes from month to month, so a single volatility is often the wrong price (experiment 2 below shows how much that costs). Based on about 24 non-overlapping 30-day windows over two years of hourly prices, so treat the size of the gap as indicative. Full reports: [SNAPSHOT.md](results/SNAPSHOT.md), [HEDGING.md](results/HEDGING.md).

---

### 1. Pricing engine (`volab/bs.py`, `volab/iv.py`)
Vectorised NumPy implementations of the Black-Scholes-Merton price and Greeks (delta, gamma, vega, theta, rho), plus **Black-76** for options on a forward price, which is how Deribit prices BTC options (each expiry against its own futures price).

**Implied volatility** has no closed form, so it is solved numerically:
- **Newton-Raphson** first: fast, using vega as the slope.
- **Bisection** when Newton would fail: short-dated, deep out-of-the-money options have almost no vega, so Newton's steps explode. Bisection is slower but guaranteed, because the price rises steadily with volatility.
- **In-the-money options are solved through put-call parity.** An in-the-money price is mostly intrinsic value, which makes volatility numerically fragile to extract. Converting it to the out-of-the-money option at the same strike gives the same volatility from a well-conditioned price. (The tests fail if this step is removed.)
- **Convergence is judged on volatility, not price.** For a cheap option, almost any volatility gets within a fraction of a cent of the price, so a price-based stopping rule returns nonsense.
- Prices outside no-arbitrage bounds, or with no measurable time value, return "no answer" rather than a made-up number.

### 2. Live smile (`volab/deribit.py`, `volab/smile.py`)
Deribit quotes option prices **in BTC**; each is converted to USD at its own underlying price, with expiry at 08:00 UTC. Our solver computes every option's implied volatility, which is then **compared with Deribit's published mark IV**: an independent check that the engine, unit conversions, and time-to-expiry are all right.

The smile uses out-of-the-money options only (puts below the forward, calls above), because those carry the price information. Per expiry, the report gives:
- **ATM implied volatility**, interpolated at the forward (never extrapolated beyond quoted strikes);
- **skew**: implied vol 10% below the forward minus 10% above. Positive means downside protection costs more.

**If Black-Scholes were right, the smile would be flat.** Its shape measures how the market disagrees with the model.

### 3. Delta hedging (`volab/hedging.py`, `volab/hedge.py`)
Sell a 30-day at-the-money call and hedge it by holding delta BTC, rebalancing on a schedule. Four experiments, each checked against theory:

| Experiment | Theory | Check |
|---|---|---|
| Rebalance daily vs every 4 h vs hourly | Hedging error ∝ 1/√(number of rebalances) | Daily/hourly error ratio ≈ √24 ≈ 4.9 |
| Realised vol ≠ the implied vol sold at | P&L ≈ vega × (implied − realised) | Simulated mean vs formula |
| Trading costs of 0, 2, 5 bps per trade | More rebalancing: less risk, more cost | The trade-off curve |
| Real hourly BTC prices vs simulated | Real prices jump and vol changes over time | Extra hedging risk the model can't see |

---

## Quick start

```bash
git clone https://github.com/saratheiranian/btc-options-vol-lab.git
cd btc-options-vol-lab
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                        # 26 tests, no network needed
```

```bash
python -m volab.snapshot                   # live smile: results/SNAPSHOT.md (takes seconds)
python -m volab.hedge --historical         # hedging experiments incl. real BTC prices: results/HEDGING.md
python -m streamlit run dashboard/app.py   # pricer, live smile, interactive hedging lab
```

Both reports come with charts. `python -m volab.hedge` without `--historical` runs the simulations fully offline.

### The dashboard
- **Pricer:** set spot, strike, days, volatility and rate to see the price, all Greeks, and a value-vs-spot chart. Enter a market price to solve for implied volatility and see which method found it.
- **Live volatility smile:** the current BTC smile by expiry, the agreement with Deribit, and the term structure.
- **Hedging lab:** sliders for rebalancing frequency, implied vs realised volatility, and trading costs, with the resulting P&L distribution.

Deploy it for free on [Streamlit Community Cloud](https://share.streamlit.io) (main file `dashboard/app.py`). No API key is needed.

---

## Tests

26 tests (`pytest -q`), all offline: Deribit is replaced by a realistic fake option chain built from a known volatility smile.
- **Prices** match a published textbook example (Hull: S=42, K=40, r=10%, σ=20%, T=0.5 gives a call of 4.76 and a put of 0.81), and put-call parity holds.
- **All five Greeks** match finite-difference derivatives, for calls and puts.
- **Implied volatility** round-trips across a 120-case grid of expiry, moneyness, volatility and option type (skipping prices too small to carry information); the bisection fallback is exercised; invalid prices return no answer.
- **Deribit conversion** recovers the known smile to within 0.0001 volatility points.
- **Hedging** matches the √N law and the vega approximation.
- **The dashboard** renders all three tabs, reacts to inputs, and survives a Deribit outage.

## Limitations

- **Black-Scholes is used as a measuring tool, not believed.** The smile and the hedging on real prices both show where it fails; modelling the smile itself (local or stochastic volatility, e.g. SABR) is a natural next step.
- **Mark prices, not trades.** Deribit's mark price is the exchange's fair-value estimate. Bid-ask spreads on illiquid strikes are wide, and the snapshot stores them for further analysis.
- **The hedging experiments ignore funding, margin, and the cost of trading the option itself,** and use r = 0, as in Deribit's convention.
- **Historical hedging uses a few dozen 30-day windows,** so treat its comparison as indicative.

---

Data: [Deribit public API](https://docs.deribit.com/) (market data only, no account); please follow Deribit's terms of use. **Research and education only. No trading, not financial advice.**

Built by Sara Ghassemi.
