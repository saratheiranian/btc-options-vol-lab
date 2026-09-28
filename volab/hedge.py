"""Run the delta-hedging experiments and write a report.

    python -m volab.hedge                  # simulated paths only (works offline)
    python -m volab.hedge --historical     # also hedge on real BTC hourly prices from Deribit

Setup: sell a 30-day at-the-money BTC call (spot 60,000 USD, implied vol 60%)
and delta-hedge it. Writes results/HEDGING.md, results/hedging.json and charts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import bs  # noqa: E402
from .hedging import gbm_paths, hedge_short_call, historical_paths, realised_vol, summarise  # noqa: E402

S0 = K = 60_000.0
DAYS = 30
T = DAYS / 365
STEPS = DAYS * 24  # hourly grid
SIGMA = 0.60
FREQUENCIES = {"Daily": 24, "Every 4 hours": 4, "Hourly": 1}


def run(out_dir: Path, n_paths: int = 4000, seed: int = 0, closes=None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = gbm_paths(S0, SIGMA, T, STEPS, n_paths, seed)
    premium = float(bs.price(S0, K, T, 0, SIGMA, "call"))
    vega = float(bs.vega(S0, K, T, 0, SIGMA))

    # 1. How often to rebalance (volatility correctly known, no costs).
    freq = {name: summarise(hedge_short_call(paths, K, T, SIGMA, rebalance_every=e)) for name, e in FREQUENCIES.items()}

    # 2. Selling at the wrong volatility (hourly hedging, no costs).
    wrong_vol = []
    for true_vol in (0.40, 0.50, 0.60, 0.70, 0.80):
        p = gbm_paths(S0, true_vol, T, STEPS, n_paths, seed + 1)
        s = summarise(hedge_short_call(p, K, T, SIGMA, rebalance_every=1))
        wrong_vol.append({"true_vol": true_vol, **s, "approx_theory": vega * (SIGMA - true_vol)})

    # 3. Costs against rebalancing frequency.
    costs = {c: {name: summarise(hedge_short_call(paths, K, T, SIGMA, rebalance_every=e, cost_bps=c))
                 for name, e in FREQUENCIES.items()} for c in (0, 2, 5)}

    # 4. Real BTC price paths (optional): fat tails and volatility that changes over time.
    historical = None
    if closes is not None:
        hist = historical_paths(closes, STEPS, S0)
        rv = realised_vol(hist, T)
        sigma_hist = float(np.median(rv))  # sell each option at the typical realised vol of the period
        sim = gbm_paths(S0, sigma_hist, T, STEPS, n_paths, seed + 2)
        historical = {
            "windows": int(len(hist)), "sigma_used": sigma_hist,
            "real": summarise(hedge_short_call(hist, K, T, sigma_hist, rebalance_every=1)),
            "gbm_same_vol": summarise(hedge_short_call(sim, K, T, sigma_hist, rebalance_every=1)),
            "realised_vol_range": [float(rv.min()), float(rv.max())],
        }

    result = {"setup": {"spot": S0, "strike": K, "days": DAYS, "implied_vol": SIGMA, "premium": premium,
                        "vega_per_vol_point": vega / 100, "paths": n_paths, "seed": seed},
              "rebalancing": freq, "wrong_vol": wrong_vol,
              "costs": {str(k): v for k, v in costs.items()}, "historical": historical}
    (out_dir / "hedging.json").write_text(json.dumps(result, indent=2))
    _charts(freq, wrong_vol, costs, out_dir)
    (out_dir / "HEDGING.md").write_text(_report(result))
    return result


def _charts(freq, wrong_vol, costs, out_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.bar(list(freq), [v["std_pnl"] for v in freq.values()], color="#2F6F62")
    ax.set(ylabel="Std. dev. of final P&L (USD)", title="Hedging error vs rebalancing frequency")
    fig.tight_layout(); fig.savefig(out_dir / "hedge_frequency.png", dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.5, 4))
    x = [w["true_vol"] * 100 for w in wrong_vol]
    ax.plot(x, [w["mean_pnl"] for w in wrong_vol], marker="o", color="#2F6F62", label="Simulated mean P&L")
    ax.plot(x, [w["approx_theory"] for w in wrong_vol], ls="--", color="#B0892B", label="vega × (implied − realised)")
    ax.axhline(0, color="grey", lw=0.8)
    ax.set(xlabel="Realised volatility (%) — option sold at 60%", ylabel="Mean P&L per option (USD)",
           title="Selling volatility: cheap or rich?")
    ax.legend(); fig.tight_layout(); fig.savefig(out_dir / "hedge_wrong_vol.png", dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.5, 4))
    for c, by_freq in costs.items():
        ax.plot(list(by_freq), [v["mean_pnl"] for v in by_freq.values()], marker="o", label=f"{c} bps per trade")
    ax.axhline(0, color="grey", lw=0.8)
    ax.set(ylabel="Mean P&L per option (USD)", title="Transaction costs vs rebalancing frequency")
    ax.legend(); fig.tight_layout(); fig.savefig(out_dir / "hedge_costs.png", dpi=130); plt.close(fig)


def _row(label: str, s: dict) -> str:
    return f"| {label} | {s['mean_pnl']:+,.0f} | {s['std_pnl']:,.0f} | {s['p5_pnl']:+,.0f} |"


def _report(r: dict) -> str:
    s, f = r["setup"], r["rebalancing"]
    freq_rows = "\n".join(f"| {k} | {v['mean_pnl']:+,.0f} | {v['std_pnl']:,.0f} | {v['std_as_pct_of_premium']:.1f}% |"
                          for k, v in f.items())
    ratio = f["Daily"]["std_pnl"] / f["Hourly"]["std_pnl"]
    wv_rows = "\n".join(f"| {w['true_vol'] * 100:.0f}% | {w['mean_pnl']:+,.0f} | {w['approx_theory']:+,.0f} |"
                        for w in r["wrong_vol"])
    cost_rows = "\n".join(
        f"| {c} | " + " | ".join(f"{v['mean_pnl']:+,.0f} (±{v['std_pnl']:,.0f})" for v in by.values()) + " |"
        for c, by in r["costs"].items())
    hist = r["historical"]
    hist_text = ("Not run. Use `python -m volab.hedge --historical` to hedge on real BTC prices from Deribit."
                 if hist is None else
                 f"""{hist['windows']} non-overlapping 30-day windows of real hourly BTC prices, each option sold at
the period's median realised volatility ({hist['sigma_used'] * 100:.1f}%; individual windows ranged from
{hist['realised_vol_range'][0] * 100:.0f}% to {hist['realised_vol_range'][1] * 100:.0f}%), hedged hourly.

| Paths | Mean P&L (USD) | Std. dev. (USD) | Worst 5% (USD) |
|---|---|---|---|
{_row("Real BTC prices", hist["real"])}
{_row("Simulated (GBM), same volatility", hist["gbm_same_vol"])}

Real prices jump and their volatility changes over time, which Black-Scholes assumes away. The gap between
the two rows is the hedging risk the model does not see. With only {hist['windows']} windows, treat it as indicative.""")
    return f"""# Delta-hedging experiments

Sell one 30-day at-the-money BTC call (spot = strike = {s['spot']:,.0f} USD, implied vol {s['implied_vol'] * 100:.0f}%,
premium {s['premium']:,.0f} USD) and delta-hedge it until expiry. {s['paths']:,} simulated price paths (seed {s['seed']}).

## 1. How often should you rebalance?

Volatility known exactly, no trading costs.

| Rebalancing | Mean P&L (USD) | Std. dev. (USD) | Std. dev. as % of premium |
|---|---|---|---|
{freq_rows}

Daily hedging is {ratio:.2f}× riskier than hourly. Theory predicts about √24 ≈ 4.90×, since hedging error shrinks
with the square root of the number of rebalances.

![Frequency](hedge_frequency.png)

## 2. What if realised volatility differs from the volatility you sold at?

Option sold at 60% implied volatility, hedged hourly.

| Realised vol | Mean P&L (USD) | Approximation: vega × (implied − realised) |
|---|---|---|
{wv_rows}

A delta-hedged option seller is really betting that volatility will be *lower* than the price implied.

![Wrong volatility](hedge_wrong_vol.png)

## 3. The cost of hedging more often

Mean P&L (± std. dev.) in USD, by trading cost per trade:

| Cost (bps) | {' | '.join(f.keys())} |
|---|---|---|---|
{cost_rows}

More frequent hedging cuts risk, but every rebalance pays costs: the best frequency depends on the cost level.

![Costs](hedge_costs.png)

## 4. Real BTC prices vs the model

{hist_text}
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--paths", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--historical", action="store_true", help="also hedge on real hourly BTC prices (needs internet)")
    ap.add_argument("--history-days", type=int, default=730)
    a = ap.parse_args()
    closes = None
    if a.historical:
        from .deribit import fetch_hourly_closes

        closes = fetch_hourly_closes(a.history_days)
        print(f"fetched {len(closes):,} hourly BTC prices")
    r = run(a.out, a.paths, a.seed, closes)
    f = r["rebalancing"]
    print(f"daily/hourly hedging-error ratio {f['Daily']['std_pnl'] / f['Hourly']['std_pnl']:.2f}. "
          f"Report: {a.out / 'HEDGING.md'}")


if __name__ == "__main__":
    main()
