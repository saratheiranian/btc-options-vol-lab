"""Delta-hedging simulator.

An option seller who holds `delta` units of the underlying, and keeps adjusting
that amount, is (in theory) left with no risk: that is the argument behind the
Black-Scholes price. In practice three things leave money on the table, and
this module measures each one:

  1. Discrete rebalancing. Hedging is only exact if adjusted continuously.
     Theory: the spread of the final P&L shrinks like 1 / sqrt(rebalances).
  2. The wrong volatility. If realised volatility differs from the implied
     volatility the option was sold at, the hedged seller gains or loses,
     roughly vega x (implied vol - realised vol).
  3. Transaction costs. Every rebalance costs money, so hedging more often
     reduces risk but increases cost: a trade-off with a sweet spot.

Setup (all prices in USD, r = 0 as in Deribit's convention): sell one call at
the implied volatility, hedge with Black-Scholes delta, settle at expiry.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import bs

HOURS_PER_YEAR = 365 * 24


def gbm_paths(S0: float, sigma: float, T: float, steps: int, n_paths: int, seed: int = 0) -> np.ndarray:
    """Geometric Brownian motion with zero drift (under r = 0). Shape (n_paths, steps + 1)."""
    rng = np.random.default_rng(seed)
    dt = T / steps
    shocks = rng.standard_normal((n_paths, steps)) * sigma * np.sqrt(dt) - 0.5 * sigma**2 * dt
    return S0 * np.exp(np.concatenate([np.zeros((n_paths, 1)), np.cumsum(shocks, axis=1)], axis=1))


def historical_paths(closes: pd.Series, steps: int, S0: float) -> np.ndarray:
    """Non-overlapping windows of real hourly prices, each rescaled to start at S0."""
    prices = closes.dropna().to_numpy()
    windows = [prices[i : i + steps + 1] for i in range(0, len(prices) - steps, steps)]
    return np.array([S0 * (w / w[0]) for w in windows])


def realised_vol(paths: np.ndarray, T: float) -> np.ndarray:
    """Annualised volatility actually realised along each path."""
    log_ret = np.diff(np.log(paths), axis=1)
    return log_ret.std(axis=1, ddof=1) * np.sqrt(log_ret.shape[1] / T)


def hedge_short_call(
    paths: np.ndarray, K: float, T: float, sigma_implied: float,
    sigma_hedge: float | None = None, rebalance_every: int = 1, cost_bps: float = 0.0,
) -> pd.DataFrame:
    """Sell a call at `sigma_implied`, delta-hedge every `rebalance_every` steps.

    Returns one row per path: premium, payoff, hedge P&L, costs, final P&L.
    """
    sigma_hedge = sigma_implied if sigma_hedge is None else sigma_hedge
    n_paths, n_points = paths.shape
    steps = n_points - 1
    dt = T / steps
    S0 = paths[:, 0]

    premium = bs.price(S0, K, T, 0.0, sigma_implied, "call")
    position = np.zeros(n_paths)          # BTC held as the hedge
    hedge_pnl = np.zeros(n_paths)
    costs = np.zeros(n_paths)
    for i in range(steps):
        S = paths[:, i]
        if i % rebalance_every == 0:
            target = bs.delta(S, K, T - i * dt, 0.0, sigma_hedge, "call")
            costs += np.abs(target - position) * S * cost_bps / 1e4
            position = target
        hedge_pnl += position * (paths[:, i + 1] - S)
    S_T = paths[:, -1]
    costs += np.abs(position) * S_T * cost_bps / 1e4  # unwind the hedge at expiry
    payoff = np.maximum(S_T - K, 0.0)
    return pd.DataFrame({
        "premium": premium, "payoff": payoff, "hedge_pnl": hedge_pnl, "costs": costs,
        "pnl": premium - payoff + hedge_pnl - costs,
        "realised_vol": realised_vol(paths, T),
    })


def summarise(result: pd.DataFrame) -> dict:
    pnl = result["pnl"]
    return {
        "mean_pnl": float(pnl.mean()), "std_pnl": float(pnl.std(ddof=1)),
        "p5_pnl": float(pnl.quantile(0.05)), "mean_costs": float(result["costs"].mean()),
        "std_as_pct_of_premium": float(pnl.std(ddof=1) / result["premium"].mean() * 100),
    }
