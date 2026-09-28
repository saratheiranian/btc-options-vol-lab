"""Implied volatility: the sigma that makes Black-Scholes match a market price.

There is no formula for it, so it is found numerically:

* Newton-Raphson first. It uses vega (the slope of price in sigma) to jump
  towards the answer and usually converges in a handful of steps.
* Bisection as a fallback. When vega is tiny (deep in/out of the money, or very
  close to expiry) Newton's steps become huge and unstable. Bisection is slower
  but cannot fail, because price is strictly increasing in sigma.

Prices outside the no-arbitrage bounds have no implied volatility at all, so the
solver returns NaN rather than a meaningless number.
"""

from __future__ import annotations

import math

import numpy as np

from . import bs

SIGMA_LO, SIGMA_HI = 1e-4, 10.0  # 0.01% to 1000% volatility
MIN_TIME_VALUE = 1e-12  # below this fraction of the price level, time value is numerical noise


def no_arbitrage_bounds(S, K, T, r, kind="call", q=0.0) -> tuple[float, float]:
    fwd_s, fwd_k = S * math.exp(-q * T), K * math.exp(-r * T)
    if kind == "call":
        return max(fwd_s - fwd_k, 0.0), fwd_s
    return max(fwd_k - fwd_s, 0.0), fwd_k


def implied_vol_details(target, S, K, T, r, kind="call", q=0.0, tol=1e-12, max_iter=50) -> dict:
    """Solve for sigma and report how: {"sigma", "method", "iterations"}.

    `tol` is the precision of sigma itself. Stopping on a small *price* error
    instead would be wrong for cheap options: with a price of $0.000001, almost
    any sigma is "close enough" in dollars.
    """
    if T <= 0 or S <= 0 or K <= 0 or not np.isfinite(target):
        return {"sigma": math.nan, "method": "invalid input", "iterations": 0}
    lower, upper = no_arbitrage_bounds(S, K, T, r, kind, q)
    if not (lower < target < upper):
        return {"sigma": math.nan, "method": "outside no-arbitrage bounds", "iterations": 0}

    # An in-the-money option is mostly intrinsic value, so its volatility is
    # numerically fragile to extract. Put-call parity turns it into the
    # out-of-the-money option with the same strike, which has the same sigma.
    fwd_s, fwd_k, via = S * math.exp(-q * T), K * math.exp(-r * T), ""
    if kind == "call" and fwd_s > fwd_k:
        target, kind, via = target - (fwd_s - fwd_k), "put", " via put-call parity"
    elif kind == "put" and fwd_k > fwd_s:
        target, kind, via = target - (fwd_k - fwd_s), "call", " via put-call parity"
    if target <= MIN_TIME_VALUE * max(S, K):
        return {"sigma": math.nan, "method": "too little time value to identify volatility", "iterations": 0}

    # Newton-Raphson from the Brenner-Subrahmanyam at-the-money approximation.
    sigma = min(max(math.sqrt(2 * math.pi / T) * target / S, 0.05), 3.0)
    for i in range(1, max_iter + 1):
        v = float(bs.vega(S, K, T, r, sigma, q))
        if v < 1e-300:
            break
        step = (float(bs.price(S, K, T, r, sigma, kind, q)) - target) / v
        if not (SIGMA_LO < sigma - step < SIGMA_HI):
            break
        sigma -= step
        if abs(step) < tol:
            return {"sigma": sigma, "method": "newton" + via, "iterations": i}

    # Bisection: guaranteed, because price rises monotonically with sigma.
    lo, hi, i = SIGMA_LO, SIGMA_HI, 0
    while hi - lo > tol and i < 200:
        i += 1
        mid = 0.5 * (lo + hi)
        if float(bs.price(S, K, T, r, mid, kind, q)) < target:
            lo = mid
        else:
            hi = mid
    return {"sigma": 0.5 * (lo + hi), "method": "bisection" + via, "iterations": i}


def implied_vol(target, S, K, T, r, kind="call", q=0.0, tol=1e-12) -> float:
    return implied_vol_details(target, S, K, T, r, kind, q, tol)["sigma"]


def implied_vol_black76(target, F, K, T, r, kind="call", tol=1e-12) -> float:
    """Implied volatility of an option on a forward price (Deribit's convention)."""
    return implied_vol(target, F, K, T, r, kind, q=r, tol=tol)


def implied_vols(targets, S, K, T, r, kinds, q=0.0) -> np.ndarray:
    """Row-by-row solver for arrays (e.g. a whole option chain)."""
    arrays = np.broadcast_arrays(np.asarray(targets, float), np.asarray(S, float),
                                 np.asarray(K, float), np.asarray(T, float))
    kinds = np.broadcast_to(np.asarray(kinds), arrays[0].shape)
    return np.array([implied_vol(p, s, k, t, r, kd, q) for p, s, k, t, kd in zip(*arrays, kinds, strict=True)])
